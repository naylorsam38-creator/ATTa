"""Isolated qualification resources (Stage 01B §14/§15).

* Disposable PostgreSQL 16 cluster (initdb + pg_ctl) with pgvector, the two application roles and fresh migrations,
  on its own port and socket directory inside the qualification workspace — production databases are never used.
* Redis, API and controller instances are started by the tests themselves on free ports (service manager).
* AIR_GAPPED: the qualification session runs inside a NEW kernel network namespace (unshare -n): only loopback
  exists, every external connection fails at the kernel; Python attempts are recorded by netaudit/sitecustomize.
* A fingerprint of production state is taken before and after to prove qualification did not change it."""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import shutil
import socket
import tempfile
import subprocess
import sys
import time
from pathlib import Path

from backend.settings import Settings
from backend.settings.loader import REPO_ROOT, resolve_path

HERE = Path(__file__).parent


class IsolationUnavailable(RuntimeError):
    pass


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def pg_bin(name: str) -> str | None:
    found = shutil.which(name)
    if found:
        return found
    base = Path("/usr/lib/postgresql")
    for d in sorted(base.glob("*/bin"), reverse=True) if base.is_dir() else []:
        if (d / name).is_file():
            return str(d / name)
    # Windows offline installs unpack the bundled PostgreSQL here (bundle install-system)
    win = REPO_ROOT / "runtime" / "system" / "postgresql" / "package" / "native" / "bin" / f"{name}.exe"
    return str(win) if win.is_file() else None


def _traversable(path: Path) -> bool:
    """True if an unprivileged account can reach `path` (every existing ancestor is world-executable)."""
    p = path.resolve()
    while True:
        if p.exists() and not (p.stat().st_mode & 0o001):
            return False
        if p.parent == p:
            return True
        p = p.parent


class DisposablePostgres:
    """A throw-away PostgreSQL cluster. Runs as the 'postgres' OS account when invoked as root (initdb refuses root)."""

    def __init__(self, workspace: Path):
        self.dir = workspace / "postgres"
        if os.geteuid() == 0 and not _traversable(workspace):
            # The cluster runs as 'postgres', which cannot enter a private (0700) workspace: use a private temp dir.
            self.dir = Path(tempfile.mkdtemp(prefix="atta-pgdata-")) / "postgres"
            self._tmp_parent = self.dir.parent
            os.chmod(self._tmp_parent, 0o755)
        self.data = self.dir / "data"
        # Unix socket paths are limited to ~107 bytes, so the socket directory lives in a short private temp dir.
        self.sock = Path(tempfile.mkdtemp(prefix="atta-pg-"))
        self.port = free_port()
        self.superuser_pw = secrets.token_urlsafe(18)
        self.migration_pw = secrets.token_urlsafe(18)
        self.app_pw = secrets.token_urlsafe(18)
        self._as = ["runuser", "-u", "postgres", "--"] if os.geteuid() == 0 else []
        for b in ("initdb", "pg_ctl", "psql"):
            if not pg_bin(b):
                raise IsolationUnavailable(f"PostgreSQL binary '{b}' not found: a disposable cluster cannot be created")

    def _run(self, cmd: list[str], **kw) -> subprocess.CompletedProcess:
        return subprocess.run(self._as + cmd, capture_output=True, text=True, timeout=120, **kw)

    def start(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        self.sock.chmod(0o700)
        pwfile = self.dir / "pw"
        pwfile.write_text(self.superuser_pw)
        if self._as:
            shutil.chown(self.dir, "postgres", "postgres")
            shutil.chown(self.sock, "postgres", "postgres")
            shutil.chown(pwfile, "postgres", "postgres")
        pwfile.chmod(0o600)
        r = self._run([pg_bin("initdb"), "-D", str(self.data), "-U", "postgres", f"--pwfile={pwfile}",
                       "--auth=scram-sha-256", "-E", "UTF8", "--no-instructions"])
        pwfile.unlink()
        if r.returncode != 0:
            raise IsolationUnavailable(f"initdb failed: {r.stderr[-500:]}")
        r = self._run([pg_bin("pg_ctl"), "-D", str(self.data), "-w", "-t", "60", "-l", str(self.dir / "server.log"),
                       "-o", f"-p {self.port} -k {self.sock} -c listen_addresses=127.0.0.1 -c fsync=off", "start"])
        if r.returncode != 0:
            raise IsolationUnavailable(f"pg_ctl start failed: {r.stderr[-500:]} {(self.dir / 'server.log').read_text()[-500:]}")
        boot = subprocess.run([pg_bin("psql"), "-h", "127.0.0.1", "-p", str(self.port), "-U", "postgres", "-d", "postgres",
                               "-v", "ON_ERROR_STOP=1", "-v", f"migration_password={self.migration_pw}",
                               "-v", f"app_password={self.app_pw}", "-f", str(REPO_ROOT / "database" / "bootstrap.sql")],
                              capture_output=True, text=True, timeout=120, env={**os.environ, "PGPASSWORD": self.superuser_pw})
        if boot.returncode != 0:
            raise IsolationUnavailable(f"bootstrap.sql failed: {boot.stderr[-800:]}")

    def env(self) -> dict[str, str]:
        return {"ATTA__DATABASE__HOST": "127.0.0.1", "ATTA__DATABASE__PORT": str(self.port),
                "ATTA__DATABASE__NAME": "atta", "ATTA__DATABASE__SSLMODE": "disable",
                "ATTA_DATABASE_PASSWORD": self.app_pw, "ATTA_DATABASE_MIGRATION_PASSWORD": self.migration_pw}

    def migrate(self, extra_env: dict) -> None:
        r = subprocess.run([sys.executable, "-m", "backend.database", "migrate"], cwd=REPO_ROOT, capture_output=True,
                           text=True, timeout=300, env={**os.environ, **extra_env, **self.env()})
        if r.returncode != 0:
            raise IsolationUnavailable(f"migrations failed in the disposable cluster: {r.stdout[-400:]} {r.stderr[-800:]}")

    def stop(self, destroy: bool = True) -> None:
        if self.data.is_dir():
            self._run([pg_bin("pg_ctl"), "-D", str(self.data), "-m", "fast", "-w", "stop"])
        shutil.rmtree(self.sock, ignore_errors=True)
        if destroy:
            shutil.rmtree(getattr(self, "_tmp_parent", None) or self.dir, ignore_errors=True)


LOCAL_CONFIG = ["configuration/local.env", "configuration/local.yaml", "configuration/operator.yaml"]


def source_copy(dest: Path, root: Path = REPO_ROOT) -> Path:
    """A private copy of the source tree for running earlier stages' unchanged tests: they write evidence and scratch
    files relative to their own location, so in a copy they can never overwrite the repository's recorded gate
    evidence. It is a shared clone (full history: regression tests diff against baseline commits) overlaid with the
    working tree as it is on disk (tracked + untracked-not-ignored files) and the git-ignored LOCAL configuration
    (database credentials etc.). Installed dependency trees (node_modules, the built web app) are linked."""
    r = subprocess.run(["git", "clone", "-q", "--shared", str(root), str(dest)], capture_output=True, text=True)
    if r.returncode != 0:
        raise IsolationUnavailable(f"source copy failed: {r.stderr[-300:]}")
    files = subprocess.run(["git", "-C", str(root), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
                           capture_output=True, check=True).stdout.split(b"\0")
    present = set()
    for raw in files:
        if not raw:
            continue
        rel = raw.decode()
        src = root / rel
        if not src.is_file() and not src.is_symlink():
            continue                                   # deleted in the working tree
        present.add(rel)
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.is_symlink() or target.exists():
            target.unlink()
        shutil.copy2(src, target, follow_symlinks=False)
    for raw in subprocess.run(["git", "-C", str(dest), "ls-files", "-z"], capture_output=True, check=True).stdout.split(b"\0"):
        rel = raw.decode()
        if rel and rel not in present and (dest / rel).is_file():
            (dest / rel).unlink()                      # deleted in the working tree: deleted in the copy
    for rel in LOCAL_CONFIG:
        if (root / rel).is_file():
            shutil.copy2(root / rel, dest / rel)
    links = [p for p in root.glob("node_modules")] + list(root.glob("apps/*/node_modules")) + \
        list(root.glob("packages/*/node_modules")) + list(root.glob("apps/web/.next"))
    for p in links:
        t = dest / p.relative_to(root)
        if not t.exists():
            t.parent.mkdir(parents=True, exist_ok=True)
            t.symlink_to(p, target_is_directory=True)
    return dest


def netns_available() -> bool:
    exe = shutil.which("unshare")
    if not exe or not sys.platform.startswith("linux"):
        return False
    try:
        return subprocess.run([exe, "-n", sys.executable, "-I", str(HERE / "netns_exec.py"), "--", "true"],
                              capture_output=True, timeout=10).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def in_new_netns(cmd: list[str]) -> list[str]:
    return [shutil.which("unshare"), "-n", sys.executable, "-I", str(HERE / "netns_exec.py"), "--", *cmd]


def external_reachable(timeout: float = 1.0) -> list[str]:
    """Probe well-known public addresses by IP (no DNS). Used INSIDE an air-gapped session: must all fail."""
    reached = []
    for host, port in (("1.1.1.1", 443), ("8.8.8.8", 53), ("9.9.9.9", 443)):
        try:
            with socket.create_connection((host, port), timeout=timeout):
                reached.append(f"{host}:{port}")
        except OSError:
            pass
    return reached


def production_fingerprint(settings: Settings) -> dict:
    """Hashes of production configuration, deployment records, service state and database contents."""
    fp: dict = {"files": {}, "database": None}
    files = [REPO_ROOT / "configuration" / n for n in ("base.yaml", "local.env", "local.yaml", "operator.yaml",
                                                        "controller.yaml", "permissions.yaml", "tool_numbers.yaml",
                                                        "capability_trust.json")]
    ddir = resolve_path("runtime/deployment")
    files += [ddir / n for n in ("active.json", "environment_manifest.json", "effective_config.json", "discovered.yaml")]
    files += [resolve_path(settings.runtime.directory) / "state" / "manager.json"]
    for f in files:
        fp["files"][str(f.relative_to(REPO_ROOT)) if REPO_ROOT in f.parents else str(f)] = \
            hashlib.sha256(f.read_bytes()).hexdigest() if f.is_file() else None
    try:
        import psycopg
        with psycopg.connect(settings.database.conninfo(read_only=True)) as c:
            rows = c.execute("SELECT n.nspname || '.' || c.relname FROM pg_class c JOIN pg_namespace n ON "
                             "n.oid = c.relnamespace WHERE c.relkind = 'r' AND n.nspname IN ('control','platform','public') "
                             "ORDER BY 1").fetchall()
            counts = {}
            for (t,) in rows:
                try:
                    counts[t] = c.execute(f"SELECT count(*), md5(coalesce(string_agg(x::text, '' ORDER BY x::text), '')) "
                                          f"FROM {t} x").fetchone()
                except Exception as exc:  # noqa: BLE001 — e.g. RLS-hidden rows: record, still comparable
                    c.rollback()
                    counts[t] = ("unreadable", type(exc).__name__)
            fp["database"] = {k: list(v) for k, v in counts.items()}
    except Exception as exc:  # noqa: BLE001
        fp["database"] = {"error": f"{type(exc).__name__}"}
    fp["sha256"] = hashlib.sha256(json.dumps(fp, sort_keys=True, default=str).encode()).hexdigest()
    return fp
