#!/usr/bin/env python3
"""Build the ATTa release zip — reproducibly, from git, checked (v117; checklist step 8).

    python3 tools/make_release.py [--out dist] [--allow-dirty]

What goes in: ONLY the files git tracks under ATTa/, as committed (a clean checkout of HEAD). Untracked files,
caches, editor files and local secrets can't ride along because they are never looked at.

What is checked before anything is written (any failure = no zip, exit 1):
  - every top-level entry is one ADM accepts (adm/config.py ALLOWED_TOP) — no stray bootstrap/cloud-init/units
  - no symlinks, no __pycache__/.pyc, no .env files, no editor/OS leftovers
  - no secrets: private keys, AWS keys, GitHub/Slack/Anthropic/Stripe-live tokens. The only exceptions are exact
    fake fixtures listed, with a reason, in tools/release_allowlist.json
  - dependencies pinned: requirements-server.txt all name==version; Docker Compose version + both sha256 pinned
  - every .py compiles, every shell script passes `bash -n`, `run` starts with #!
What is written:
  - ATTa/MANIFEST.sha256: every file's sha256 (ADM refuses a bundle that doesn't match it)
  - ATTa/release.json gains source_commit (the commit it was built from)
  - dist/ATTa-<version>-<commit12>.zip + .sha256 (sha256sum format) + .manifest.sha256 (the manifest's hash)
The zip is deterministic: entries sorted, timestamps = the commit's time, fixed modes (0755 for the few files that
are run directly, 0644 otherwise). Build the same commit twice: the same bytes (CI checks it).
Last, the zip is opened again and put through ADM's OWN staging checks (find_root, check, manifest).
"""
from __future__ import annotations
import argparse, hashlib, io, json, os, re, subprocess, sys, tempfile, zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
BUNDLE = "ATTa"
EXECUTABLE = {"run", "04-deployment/deployd/deployctl", "04-deployment/deployd/deployd.py"}
EXEC_SUFFIXES = (".sh",)
FORBIDDEN_NAME = re.compile(r"(^|/)(__pycache__|\.DS_Store|Thumbs\.db|\.idea|\.vscode)(/|$)|\.pyc$|~$|\.sw[op]$|"
                            r"(^|/)\.env($|\.)(?!example)")
SECRET_PATTERNS = {   # (pattern, allowed in tests/?)
    "private key": (re.compile(r"-----BEGIN (RSA |EC |OPENSSH |DSA |ENCRYPTED )?PRIVATE KEY-----"), False),
    "AWS access key": (re.compile(r"\b(AKIA|ASIA)[0-9A-Z]{16}\b"), False),
    "GitHub token": (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b"), False),
    "Slack token": (re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b"), False),
    "Anthropic API key": (re.compile(r"\bsk-ant-api\d\d-[A-Za-z0-9_-]{20,}\b"), False),
    "Stripe live key": (re.compile(r"\b[sr]k_live_[0-9A-Za-z]{20,}\b"), True),
}
TEXT_SUFFIXES = {".py", ".sh", ".md", ".txt", ".json", ".yml", ".yaml", ".conf", ".html", ".js", ".css", ".service",
                 ".toml", ".cfg", ".ini", ""}


class ReleaseError(Exception):
    pass


def git(*args, binary=False):
    r = subprocess.run(["git", "-C", str(REPO), *args], capture_output=True, check=True)
    return r.stdout if binary else r.stdout.decode()


def tracked_files():
    """{path under ATTa/: bytes as committed at HEAD}"""
    out = {}
    for line in git("ls-tree", "-r", "-z", "--full-tree", "HEAD", BUNDLE).split("\0"):
        if not line:
            continue
        meta, path = line.split("\t", 1)
        mode, kind, sha = meta.split()
        if kind != "blob":
            raise ReleaseError(f"{path}: not a regular file in git ({kind})")
        if mode == "120000":
            raise ReleaseError(f"{path}: a symlink (bundles carry regular files only)")
        out[path[len(BUNDLE) + 1:]] = git("cat-file", "blob", sha, binary=True)
    return out


def allowlist():
    doc = json.loads((REPO / "tools" / "release_allowlist.json").read_text())
    return {(e["file"], e["match"]) for e in doc["entries"]}


def check(files, allowed_top):
    problems = []
    allowed = allowlist()
    for rel, data in files.items():
        top = rel.split("/", 1)[0]
        if top not in allowed_top:
            problems.append(f"{rel}: '{top}' is not something a bundle may hold (adm/config.py ALLOWED_TOP)")
        if FORBIDDEN_NAME.search(rel):
            problems.append(f"{rel}: not shipped (cache, editor/OS file, or an env file)")
        if Path(rel).suffix in TEXT_SUFFIXES or rel == "run":
            text = data.decode("utf-8", "replace")
            for what, (rx, ok_in_tests) in SECRET_PATTERNS.items():
                if ok_in_tests and rel.startswith("tests/"):
                    continue
                hits = {m.group(0) for m in rx.finditer(text)} - {m for f, m in allowed if f == rel}
                if hits:
                    problems.append(f"{rel}: looks like it contains a {what} (tools/release_allowlist.json lists "
                                    "the known fake fixtures)")
    req = files.get("04-deployment/requirements-server.txt", b"").decode()
    for l in (l.strip() for l in req.splitlines()):
        if l and not l.startswith("#") and not re.fullmatch(r"[A-Za-z0-9_.-]+==[0-9][0-9A-Za-z.]*", l):
            problems.append(f"requirements-server.txt: '{l}' is not pinned as name==version")
    lib = files.get("04-deployment/bootstrap-lib.sh", b"").decode()
    if not re.search(r'ATTA_COMPOSE_VERSION="\$\{ATTA_COMPOSE_VERSION:-v[0-9.]+\}"', lib) or \
            len(re.findall(r'ATTA_COMPOSE_SHA256_[A-Z0-9_]+="[0-9a-f]{64}"', lib)) != 2:
        problems.append("bootstrap-lib.sh: Docker Compose version/sha256 are not pinned")
    if not files.get("run", b"").startswith(b"#!"):
        problems.append("run: does not start with #!")
    with tempfile.TemporaryDirectory() as d:
        for rel, data in files.items():
            if rel.endswith(".py"):
                try:
                    compile(data, rel, "exec")
                except SyntaxError as e:
                    problems.append(f"{rel}: does not compile ({e.msg}, line {e.lineno})")
            elif rel.endswith(".sh") or rel == "run":
                f = Path(d) / "x.sh"; f.write_bytes(data)
                r = subprocess.run(["bash", "-n", str(f)], capture_output=True, text=True)
                if r.returncode:
                    problems.append(f"{rel}: bash -n: {r.stderr.strip()[:200]}")
    return problems


def mode_of(rel):
    return 0o755 if rel in EXECUTABLE or rel.endswith(EXEC_SUFFIXES) else 0o644


def build(files, commit, commit_time):
    rj = json.loads(files["release.json"])
    rj["source_commit"] = commit
    files = dict(files)
    files["release.json"] = (json.dumps(rj, indent=2) + "\n").encode()
    manifest = "".join(f"{hashlib.sha256(files[r]).hexdigest()}  {r}\n" for r in sorted(files)).encode()
    files["MANIFEST.sha256"] = manifest
    # zip timestamps: the commit's time (UTC), never earlier than 1980 (the zip format's epoch)
    import time
    ts = time.gmtime(max(commit_time, 315532800))[:6]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for d in sorted({"/".join(r.split("/")[:i]) for r in files for i in range(1, r.count("/") + 1)}):
            zi = zipfile.ZipInfo(f"{BUNDLE}/{d}/", ts)
            zi.external_attr = (0o40755 << 16) | 0x10
            zi.create_system = 3
            z.writestr(zi, b"")
        for rel in sorted(files):
            zi = zipfile.ZipInfo(f"{BUNDLE}/{rel}", ts)
            zi.external_attr = (0o100000 | mode_of(rel)) << 16
            zi.create_system = 3
            zi.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(zi, files[rel], compresslevel=9)
    return buf.getvalue(), rj["version"], hashlib.sha256(manifest).hexdigest()


def verify(zip_bytes):
    """Open the zip again: safe names only, then ADM's own staging checks on the extracted bundle."""
    sys.path.insert(0, str(REPO / BUNDLE / "04-deployment" / "deployd"))
    from adm import staging
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
        for i in z.infolist():
            n = i.filename
            if n.startswith("/") or ".." in n.split("/") or "\\" in n or not n.startswith(BUNDLE + "/"):
                raise ReleaseError(f"unsafe name in the zip: {n}")
            if (i.external_attr >> 16) & 0o170000 == 0o120000:
                raise ReleaseError(f"symlink in the zip: {n}")
        with tempfile.TemporaryDirectory() as d:
            z.extractall(d)
            root = staging.find_root(Path(d))
            version = staging.check(root)
            if not staging.verify_manifest(root):
                raise ReleaseError("the manifest was not verified")
            return version


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default=str(REPO / "dist"))
    ap.add_argument("--allow-dirty", action="store_true", help="build HEAD even if the working tree has changes")
    a = ap.parse_args(argv)
    try:
        dirty = git("status", "--porcelain", "--", BUNDLE).strip()
        if dirty and not a.allow_dirty:
            raise ReleaseError("the working tree has uncommitted changes under ATTa/ (a release is built from a commit):\n"
                               + dirty[:800])
        commit = git("rev-parse", "HEAD").strip()
        commit_time = int(git("show", "-s", "--format=%ct", "HEAD").strip())
        files = tracked_files()
        sys.path.insert(0, str(REPO / BUNDLE / "04-deployment" / "deployd"))
        from adm import config
        problems = check(files, config.ALLOWED_TOP)
        if problems:
            raise ReleaseError("refused:\n  " + "\n  ".join(problems))
        data, version, manifest_sha = build(files, commit, commit_time)
        try:
            checked = verify(data)
        except Exception as e:   # ADM's own BundleRejected (or anything else) = no release
            raise ReleaseError(f"the built zip fails ADM's staging checks: {e}")
        if checked != version:
            raise ReleaseError(f"ADM read version {checked!r}, expected {version!r}")
        out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
        name = f"ATTa-{version}-{commit[:12]}.zip"
        (out / name).write_bytes(data)
        digest = hashlib.sha256(data).hexdigest()
        (out / (name + ".sha256")).write_text(f"{digest}  {name}\n")
        (out / (name + ".manifest.sha256")).write_text(f"{manifest_sha}  {BUNDLE}/MANIFEST.sha256\n")
        print(f"{out / name}\nsha256 {digest}\nmanifest {manifest_sha}\nfiles {len(files) + 1}  commit {commit}")
        return 0
    except (ReleaseError, subprocess.CalledProcessError) as e:
        print(f"make_release: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
