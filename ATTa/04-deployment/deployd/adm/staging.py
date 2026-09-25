"""Stage and check a bundle before anything live is touched.
A bundle is refused here (never activated) if it lacks a required file, its release.json is
unreadable, or any Python file in 04-deployment fails to compile. The first FAIL line says why."""
from pathlib import Path
import json, py_compile, shutil, stat, zipfile
from . import config


class BundleRejected(RuntimeError):
    pass


def extract(archive, job):
    d = config.STAGING / job
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True)
    root = d.resolve()
    with zipfile.ZipFile(archive) as z:
        infos = z.infolist()
        if len(infos) > config.MAX_BUNDLE_FILES:
            raise BundleRejected(f"too many files in zip: {len(infos)} (limit {config.MAX_BUNDLE_FILES})")
        declared = 0
        for info in infos:
            # v116: a bundle carries files, never links or devices (zipfile would write them out as files anyway)
            kind = (info.external_attr >> 16) & 0o170000
            if kind and kind not in (stat.S_IFREG, stat.S_IFDIR):
                raise BundleRejected(f"not a plain file in zip: {info.filename}")
            # Refuse path traversal: every member must land inside the staging folder.
            target = (d / info.filename).resolve()
            if root not in target.parents and target != root:
                raise BundleRejected(f"unsafe path in zip: {info.filename}")
            declared += info.file_size
            if info.compress_size and info.file_size / info.compress_size > config.MAX_COMPRESSION_RATIO and info.file_size > 1024**2:
                raise BundleRejected(f"suspicious compression ratio in zip: {info.filename}")
        if declared > config.MAX_BUNDLE_BYTES:
            raise BundleRejected(f"zip would unpack to {declared} bytes (limit {config.MAX_BUNDLE_BYTES})")
        # Extract member by member and count the bytes actually written, so a header that lies
        # about its size still can't push past the limit.
        written = 0
        for info in infos:
            target = (d / info.filename).resolve()
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True); continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with z.open(info) as src, open(target, "wb") as dst:
                while True:
                    chunk = src.read(1024 * 1024)
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > config.MAX_BUNDLE_BYTES:
                        raise BundleRejected(f"zip unpacked past the size limit ({config.MAX_BUNDLE_BYTES} bytes)")
                    dst.write(chunk)
    return d


def find_root(staged):
    """The bundle root is the folder that holds `run` AND release.json AND 04-deployment/.
    The zip may wrap it in one folder (ATTa/) or not; both are accepted. Several = refused."""
    hits = [p.parent for p in staged.rglob("release.json")
            if (p.parent / "run").is_file() and (p.parent / "04-deployment").is_dir()]
    if not hits:
        raise BundleRejected("not an ATTa bundle: no folder holds run + release.json + 04-deployment/")
    if len(hits) > 1:
        raise BundleRejected("ambiguous bundle: several folders look like an ATTa root: " + ", ".join(str(h) for h in hits))
    root = hits[0]
    # v116: nothing may ride along beside the bundle's own folder (the v114.2 zip carried a second bootstrap.sh,
    # a systemd unit and scripts next to ATTa/), and the folder may hold only what a bundle is made of.
    outside = [p for p in staged.rglob("*") if root not in p.parents and p != root and p not in root.parents]
    if outside:
        raise BundleRejected("files outside the bundle's own folder: " + ", ".join(sorted(str(p.relative_to(staged)) for p in outside)[:8]))
    unknown = sorted(p.name for p in root.iterdir() if p.name not in config.ALLOWED_TOP)
    if unknown:
        raise BundleRejected("unexpected entries in the bundle folder: " + ", ".join(unknown[:8]))
    return root


def check(root):
    """Hard checks. Returns the bundle's version string. Raises BundleRejected with the reason."""
    missing = [f for f in config.REQUIRED_FILES if not (root / f).is_file()]
    if missing:
        raise BundleRejected("missing required files: " + ", ".join(missing))
    try:
        rel = json.loads((root / "release.json").read_text())
        version = str(rel["version"])
    except (OSError, ValueError, KeyError) as e:
        raise BundleRejected(f"release.json unreadable or has no version: {e}")
    bad = []
    for py in sorted((root / "04-deployment").rglob("*.py")):
        try:
            py_compile.compile(str(py), doraise=True)
        except py_compile.PyCompileError as e:
            bad.append(f"{py.relative_to(root)}: {e.msg.splitlines()[-1] if e.msg else e}")
    if bad:
        raise BundleRejected("python does not compile: " + " | ".join(bad))
    run = root / "run"
    if not run.read_bytes().startswith(b"#!"):
        raise BundleRejected("`run` does not start with #! (something was added before it)")
    run.chmod(run.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP)
    for sh in (root / "04-deployment").glob("*.sh"):
        sh.chmod(sh.stat().st_mode | stat.S_IXUSR)
    return version


def run_tests(root, log):
    """v116: the bundle's own test suite (security tests included) must pass before it is activated. Run as an
    unprivileged user with an empty environment and throwaway folders: never root, never the live data."""
    import os, pwd, subprocess, tempfile
    tests = root / "tests"
    if not tests.is_dir():
        raise BundleRejected("the bundle has no tests/ folder; a bundle is only deployed after its tests pass")
    try:
        u = pwd.getpwnam(config.TEST_USER)
    except KeyError:
        raise BundleRejected(f"test user {config.TEST_USER!r} does not exist (ATTA_ADM_TEST_USER)")
    work = Path(tempfile.mkdtemp(prefix="atta-bundle-tests-"))
    os.chmod(work, 0o700)
    if os.geteuid() == 0:
        os.chown(work, u.pw_uid, u.pw_gid)
    # The test user must be able to read the bundle: open the staging folders down to it (traverse only) and
    # the bundle itself (read-only). It is code, not secrets, and the staging copy is discarded afterwards.
    staging_top = config.STAGING.resolve()
    for d in [p for p in root.resolve().parents if p == staging_top or staging_top in p.parents]:
        os.chmod(d, os.stat(d).st_mode | 0o001)
    subprocess.run(["chmod", "-R", "a+rX", str(root)], check=False)
    # (only when dropping to another user; run unprivileged already, the tests run as that same user)
    blocked = [str(d) for d in root.resolve().parents if not os.stat(d).st_mode & 0o001] if os.geteuid() == 0 else []
    if blocked:
        raise BundleRejected(f"the test user cannot reach the bundle: {blocked[0]} is not passable (needs o+x); "
                             "keep ADM under the data folder or open that folder for traversal")
    env = {"PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C.UTF-8",
           "HOME": str(work), "TMPDIR": str(work), "PYTHONDONTWRITEBYTECODE": "1",
           "APP_BUILDER_ROOT": str(work / "root"), "ATTA_ADM_ROOT": str(work / "adm")}
    cmd = ["python3", "-m", "unittest", "discover", "-s", "tests"]
    if os.geteuid() == 0:
        cmd = ["setpriv", f"--reuid={u.pw_uid}", f"--regid={u.pw_gid}", "--clear-groups", "--no-new-privs"] + cmd
    log.write(f"=== bundle tests (as {config.TEST_USER}): {' '.join(cmd[-5:])}\n"); log.flush()
    try:
        r = subprocess.run(cmd, cwd=str(root), env=env, capture_output=True, text=True, timeout=config.TEST_TIMEOUT)
    except subprocess.TimeoutExpired:
        raise BundleRejected(f"bundle tests did not finish within {config.TEST_TIMEOUT}s")
    finally:
        subprocess.run(["rm", "-rf", str(work)], check=False)
    out = (r.stdout or "") + (r.stderr or "")
    log.write(out[-20000:] + "\n"); log.flush()
    tail = [l for l in out.splitlines() if l.startswith(("Ran ", "OK", "FAILED", "FAIL:", "ERROR:"))]
    if r.returncode != 0:
        why = tail[-6:] or [f"exit {r.returncode}: " + " / ".join(l.strip() for l in out.strip().splitlines()[-2:])]
        raise BundleRejected("bundle tests failed: " + " | ".join(why))
    return next((l for l in reversed(tail) if l.startswith("Ran ")), "tests passed")


def discard(job):
    shutil.rmtree(config.STAGING / job, ignore_errors=True)
