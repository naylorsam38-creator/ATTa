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
    return hits[0]


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
    run.chmod(run.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP)
    for sh in (root / "04-deployment").glob("*.sh"):
        sh.chmod(sh.stat().st_mode | stat.S_IXUSR)
    return version


def discard(job):
    shutil.rmtree(config.STAGING / job, ignore_errors=True)
