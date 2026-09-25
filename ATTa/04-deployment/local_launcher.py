#!/usr/bin/env python3
"""The laptop side of `bash run` (v117, checklists H, I, J): start, stop and report a LOCAL ATTa honestly.

    python3 local_launcher.py start | stop | status        (the `run` script calls this; it is not for servers)

Up to v116 the local launcher sourced .env as shell, printed RUNNING when only the gateway's port answered (the
pipeline could be dead), called "Already running" on any program answering /health on 8787, killed whatever pid a
stale pid file named, and let two starts race into duplicate pipelines. Now:

  - .env is read as data (envfile.py); the services get it as their environment, never through a shell.
  - Every process is recorded with its pid, start time, command line and process group (run-state.json). A pid is
    only ever signalled when all of them still match, so a stale file or a reused pid can't hit another program.
    Stop takes down the whole process group (the service and everything it started), then confirms it is gone.
  - RUNNING means: the gateway proves it is THIS instance (atta_health identity check on the .env port) AND the
    pipeline is alive, is the one recorded, and writes its heartbeat. Anything less is FAILED/DEGRADED, with the
    reason and exit codes kept in run-state.json, and a non-zero exit.
  - One lock covers start and stop, so two starts, or a start and a stop, never interleave; the pipeline itself
    also refuses to run twice on one data folder. The lock is the kernel's (flock): it can't go stale.
  - A port held by something else is reported at once, before anything is started.
Exit codes: 0 running/stopped as asked; 1 failed; 3 (status) not running; 4 (status) degraded.
"""
from __future__ import annotations
import fcntl, json, os, secrets, signal, socket, subprocess, sys, time
from pathlib import Path

DEP = Path(__file__).resolve().parent
sys.path.insert(0, str(DEP))
import envfile  # noqa: E402

SERVICES = ("gateway", "pipeline")
START_TIMEOUT = float(os.environ.get("ATTA_LOCAL_START_TIMEOUT", "40"))
STOP_GRACE = float(os.environ.get("ATTA_LOCAL_STOP_GRACE", "10"))
LOCK_WAIT = float(os.environ.get("ATTA_LOCAL_LOCK_WAIT", "60"))
HEARTBEAT_MAX_AGE = 30.0


def root_dir() -> Path:
    return Path(os.environ.get("APP_BUILDER_ROOT") or Path.home() / ".app-builder-local")


# ------------------------------------------------------------------------------------------ process identity
def _ps(pid, field):
    r = subprocess.run(["ps", "-o", f"{field}=", "-p", str(pid)], capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else None


def _proc(pid):
    """(start token, command line, state) of a pid that exists (zombies included), or None."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return None
    if pid <= 1:
        return None
    if Path("/proc/self/stat").exists():
        try:
            data = Path(f"/proc/{pid}/stat").read_bytes().decode("utf-8", "replace")
            rest = data[data.rfind(")") + 2:].split()
            cmd = Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode("utf-8", "replace").strip()
            return rest[19], cmd, rest[0]
        except (OSError, IndexError):
            return None
    start, cmd, state = _ps(pid, "lstart"), _ps(pid, "command"), _ps(pid, "state")
    return (start, cmd or "", state or "") if start else None


def identity(pid):
    """(start token, command line) of a RUNNING pid, or None. Linux: /proc; macOS and others: ps."""
    p = _proc(pid)
    if p is None or p[2][:1] in ("Z", "X") or not p[1]:
        return None
    return p[0], p[1]


def matches(rec) -> bool:
    """May we signal it? True only if the recorded process is still the same process: alive, same start time,
    same command line."""
    if not isinstance(rec, dict):
        return False
    ident = identity(rec.get("pid"))
    return ident is not None and ident[0] == rec.get("start") and ident[1] == rec.get("cmdline")


def still_running(rec) -> bool:
    """Is it gone yet? The same process (same start time) that has not finished exiting. Its command line is NOT
    used: a dying process loses it (the kernel frees its memory) BEFORE it closes its sockets, so a check on the
    command line would call a gateway gone while its port still accepts connections."""
    p = _proc(rec.get("pid")) if isinstance(rec, dict) else None
    return p is not None and p[0] == rec.get("start") and p[2][:1] not in ("Z", "X")


def group_members(pgid, script=None):
    """Processes in process group `pgid` that are not zombies (the group is ours: we created it)."""
    out = []
    r = subprocess.run(["ps", "-A", "-o", "pid=,pgid=,stat=,command="], capture_output=True, text=True)
    for line in r.stdout.splitlines():
        parts = line.split(None, 3)
        if len(parts) >= 3 and parts[1] == str(pgid) and parts[0] != str(os.getpid()) and not parts[2].startswith("Z"):
            out.append((int(parts[0]), parts[3] if len(parts) == 4 else ""))
    return out


# ------------------------------------------------------------------------------------------ state + lock
class Launcher:
    def __init__(self, root: Path):
        self.root = root
        self.env_file = root / ".env"
        self.state_file = root / "run-state.json"
        self.lock_file = root / "launcher.lock"
        self._lock_fd = None

    def lock(self):
        self.root.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.lock_file, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o600)
        end = time.monotonic() + LOCK_WAIT
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                self._lock_fd = fd
                return
            except BlockingIOError:
                if time.monotonic() > end:
                    os.close(fd)
                    raise SystemExit(say("another `bash run` start/stop is still running for this folder; try again", 1))
                time.sleep(0.2)

    def unlock(self):
        if self._lock_fd is not None:
            fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
            os.close(self._lock_fd)
            self._lock_fd = None

    def load(self):
        try:
            return json.loads(self.state_file.read_text())
        except (OSError, ValueError):
            return {}

    def save(self, st):
        st["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        tmp = self.state_file.with_name(self.state_file.name + ".tmp")
        tmp.write_text(json.dumps(st, indent=2) + "\n")
        os.chmod(tmp, 0o600)
        os.replace(tmp, self.state_file)

    # -------------------------------------------------------------------------------------- .env
    def ensure_env(self):
        """The local .env: made once (0600, a new session secret), read as data. An older one without a session
        secret (from before local logins) is kept aside as .env.old-<time>, never silently deleted."""
        self.root.mkdir(parents=True, exist_ok=True)
        if self.env_file.exists():
            try:
                vals, _ = envfile.load(str(self.env_file))
            except (OSError, envfile.EnvFileError) as e:
                raise SystemExit(say(f"FAILED: {self.env_file}: {e}. Fix that line (it is data, never run as shell).", 1))
            if vals.get("APP_BUILDER_SESSION_SECRET"):
                return vals
            aside = self.env_file.with_name(f".env.old-{time.strftime('%Y%m%dT%H%M%S')}")
            os.replace(self.env_file, aside)
            say(f"note: {self.env_file} had no session secret (made before local logins); kept as {aside.name}")
        text = (f"APP_BUILDER_ROOT={self.root}\nAPP_BUILDER_HOST=127.0.0.1\nAPP_BUILDER_PORT=8787\n"
                f"APP_BUILDER_SESSION_SECRET={secrets.token_urlsafe(48)}\n"
                "# Browser check on, as on the server: without Playwright a build ends NOT_QUALIFIED, never falsely QUALIFIED.\n"
                "# Install it with: python3 -m pip install playwright && python3 -m playwright install chromium\n"
                "APP_BUILDER_BROWSER_CHECK=true\nAPP_BUILDER_WATCHER_INTERVAL=300\nAPP_BUILDER_TEST_ACCOUNTS=10\n"
                "COOLIFY_URL=\nCOOLIFY_TOKEN=\n")
        if not envfile.UNQUOTED_RE.fullmatch(str(self.root)):
            text = text.replace(f"APP_BUILDER_ROOT={self.root}\n", f"APP_BUILDER_ROOT='{self.root}'\n")
        tmp = self.env_file.with_name(".env.new")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(text)
        os.replace(tmp, self.env_file)
        vals, _ = envfile.load(str(self.env_file))
        return vals

    def child_env(self, vals):
        env = {k: v for k, v in os.environ.items() if not k.startswith(("APP_BUILDER_", "ATTA_", "COOLIFY_"))}
        env.update(vals)
        env["APP_BUILDER_ROOT"] = str(self.root)
        env.pop("INVOCATION_ID", None)
        return env

    # -------------------------------------------------------------------------------------- inspection
    def port_of(self, vals):
        try:
            return int(vals.get("APP_BUILDER_PORT") or 8787)
        except ValueError:
            raise SystemExit(say(f"FAILED: APP_BUILDER_PORT in {self.env_file} is not a number", 1))

    def gateway_ok(self, timeout=0.0):
        import atta_health
        ok, lines = atta_health.run_checks(self.env_file, direct=True, timeout=timeout, interval=0.5)
        return ok, lines[-1] if lines else ""

    def pipeline_ok(self, rec):
        try:
            hb = json.loads((self.root / "state" / "pipeline.heartbeat").read_text())
        except (OSError, ValueError):
            return False, "no heartbeat yet"
        if hb.get("pid") != rec.get("pid"):
            return False, f"heartbeat is from pid {hb.get('pid')}, not {rec.get('pid')}"
        if time.time() - float(hb.get("at", 0)) > HEARTBEAT_MAX_AGE:
            return False, f"no heartbeat for {time.time() - float(hb.get('at', 0)):.0f}s"
        return True, "heartbeat fresh"

    def inspect(self, st):
        """{service: 'up' | reason it is not}"""
        out = {}
        for name in SERVICES:
            rec = (st.get("services") or {}).get(name)
            if not rec:
                out[name] = "not started"
            elif not matches(rec):
                out[name] = "exited" if identity(rec.get("pid")) is None else "pid now belongs to another program"
            else:
                out[name] = "up"
        return out

    # -------------------------------------------------------------------------------------- stop
    def stop_service(self, name, rec):
        """Stop one recorded service and its whole process group. Returns a problem string, or None."""
        pgid = rec.get("pgid")
        if matches(rec):
            targets = "group"
            def gone():
                return not still_running(rec) and not group_members(pgid)
        else:
            # The leader is gone (or its pid reused). What is still in its group AND runs our code is ours; anything
            # else is left alone.
            members = [(p, c) for p, c in group_members(pgid) if str(DEP) in c]
            if not members:
                return None
            targets = [p for p, _ in members]
            starts = {p: (_proc(p) or ("",))[0] for p in targets}
            def gone():
                return not any(still_running({"pid": p, "start": st}) for p, st in starts.items())
        for sig, wait in ((signal.SIGTERM, STOP_GRACE), (signal.SIGKILL, 5)):
            try:
                if targets == "group":
                    os.killpg(pgid, sig)
                else:
                    for p in targets:
                        os.kill(p, sig)
            except (ProcessLookupError, PermissionError):
                pass
            end = time.monotonic() + wait
            while time.monotonic() < end:
                if gone():
                    return None
                time.sleep(0.1)
        return None if gone() else f"{name}: still running after SIGKILL (pid {rec.get('pid')}, group {pgid})"

    def stop_all(self, st):
        problems = []
        for name in reversed(SERVICES):
            rec = (st.get("services") or {}).get(name)
            if rec:
                p = self.stop_service(name, rec)
                if p:
                    problems.append(p)
        return problems

    # -------------------------------------------------------------------------------------- start
    def spawn(self, name, env):
        script = DEP / f"{name}.py"
        log = open(self.root / f"{name}.log", "ab")
        p = subprocess.Popen([sys.executable, str(script)], cwd=str(self.root), env=env, stdin=subprocess.DEVNULL,
                             stdout=log, stderr=subprocess.STDOUT, start_new_session=True, close_fds=True)
        log.close()
        ident = None
        for _ in range(50):
            ident = identity(p.pid)
            if ident and str(script) in ident[1]:
                break
            time.sleep(0.02)
        if not ident:
            p.wait(5)
            return p, None
        return p, {"pid": p.pid, "pgid": p.pid, "start": ident[0], "cmdline": ident[1], "script": str(script),
                   "started_at": time.time()}

    def start(self):
        vals = self.ensure_env()
        port = self.port_of(vals)
        st = self.load()
        state = self.inspect(st)
        if st.get("status") == "RUNNING" and all(v == "up" for v in state.values()):
            ok, why = self.gateway_ok(timeout=5)
            if ok and self.pipeline_ok(st["services"]["pipeline"])[0]:
                return self.report_running(port, already=True)
        if any(v == "up" for v in state.values()):
            say("A previous local instance is only partly running " + str(state) + "; stopping it first.")
        problems = self.stop_all(st)
        if problems:
            return self.fail(st, "could not stop the previous instance: " + "; ".join(problems))
        if st.get("services"):
            # We just stopped our own gateway: give the kernel a moment to release its port before judging it.
            end = time.monotonic() + 5
            while _port_busy("127.0.0.1", port) and time.monotonic() < end:
                time.sleep(0.1)
        if _port_busy("127.0.0.1", port):
            ok, _ = self.gateway_ok(timeout=0)
            who = ("an ATTa gateway for this folder that this launcher did not start (stop it, then run again)" if ok
                   else "another program")
            return self.fail({}, f"port {port} (APP_BUILDER_PORT) is already in use by {who}; nothing was started")
        env = self.child_env(vals)
        r = subprocess.run([sys.executable, str(DEP / "accounts.py"), "init"], env=env, cwd=str(self.root),
                           capture_output=True, text=True)
        if r.returncode:
            return self.fail({}, "accounts init failed: " + (r.stderr or r.stdout).strip()[-400:])
        front = DEP.parent / "02-front-door" / "front-door.html"
        if front.is_file():
            (self.root / "front-door.html").write_bytes(front.read_bytes())
        if (DEP / "upstream_apps.json").is_file():
            (self.root / "upstream_apps.json").write_bytes((DEP / "upstream_apps.json").read_bytes())
        for d in ("inbox", "state", "state/apps"):
            (self.root / d).mkdir(parents=True, exist_ok=True)
        (self.root / "state" / "pipeline.heartbeat").unlink(missing_ok=True)
        st = {"status": "STARTING", "root": str(self.root), "port": port, "services": {}, "failure": None,
              "started_at": time.time()}
        procs = {}
        for name in SERVICES:
            p, rec = self.spawn(name, env)
            if rec is None:
                st["services"][name] = {"pid": p.pid, "exit": p.returncode}
                return self.fail(st, f"{name} exited at once (exit {p.returncode}); last lines of {name}.log:\n"
                                 + _tail(self.root / f"{name}.log"), procs)
            st["services"][name] = rec
            procs[name] = p
        self.save(st)
        end = time.monotonic() + START_TIMEOUT
        why = "timed out"
        while time.monotonic() < end:
            for name, p in procs.items():
                if p.poll() is not None:
                    st["services"][name]["exit"] = p.returncode
                    return self.fail(st, f"{name} exited during start (exit {p.returncode}); last lines of {name}.log:\n"
                                     + _tail(self.root / f"{name}.log"), procs)
            gw_ok, gw_why = self.gateway_ok(timeout=0)
            pl_ok, pl_why = self.pipeline_ok(st["services"]["pipeline"])
            if gw_ok and pl_ok:
                st["status"] = "RUNNING"
                self.save(st)
                return self.report_running(port)
            why = f"gateway: {gw_why or 'ok'}; pipeline: {pl_why}"
            time.sleep(0.5)
        return self.fail(st, f"not ready within {START_TIMEOUT:.0f}s ({why})", procs)

    def fail(self, st, reason, procs=None):
        if st.get("services"):
            self.stop_all(st)
        for p in (procs or {}).values():
            try:
                p.wait(1)
            except subprocess.TimeoutExpired:
                pass
        st.update(status="FAILED", failure={"reason": reason, "at": time.time()})
        self.save(st)
        say("FAILED (local instance): " + reason)
        say("Nothing is left running. Details: " + str(self.state_file))
        return 1

    def report_running(self, port, already=False):
        line()
        say("RUNNING (local instance" + (", already up" if already else "") + "): gateway proves it is this instance, "
            "pipeline alive with a fresh heartbeat")
        line()
        say(f"  Open:      http://127.0.0.1:{port}/")
        say(f"  Logins:    {self.root / 'TEST_ACCOUNTS.txt'}   (admin + tester01..tester10)")
        say("  Stop it:   bash run stop        Check it:  bash run status")
        line()
        say("Done for a laptop. NOT done for the public internet — a live public URL")
        say("still needs the AWS server (domain + TLS + reachable host). Run this same")
        say("command on that server and it will do the full deploy automatically.")
        return 0

    def stop(self):
        st = self.load()
        if not st.get("services"):
            self.state_file.unlink(missing_ok=True)
            say("Stopped (nothing was running from this folder).")
            return 0
        problems = self.stop_all(st)
        if problems:
            st["failure"] = {"reason": "stop: " + "; ".join(problems), "at": time.time()}
            self.save(st)
            say("NOT STOPPED: " + "; ".join(problems))
            return 1
        self.state_file.unlink(missing_ok=True)
        (self.root / "state" / "pipeline.heartbeat").unlink(missing_ok=True)
        say("Stopped.")
        return 0

    def status(self):
        st = self.load()
        if not st.get("services"):
            say(f"Local instance: down.  Start it with:  bash run" + (f"   (last failure: {st['failure']['reason'].splitlines()[0]})"
                                                                    if st.get("failure") else ""))
            return 3
        state = self.inspect(st)
        gw = self.gateway_ok(timeout=0) if state.get("gateway") == "up" else (False, "")
        pl = self.pipeline_ok(st["services"]["pipeline"]) if state.get("pipeline") == "up" else (False, "")
        port = st.get("port")
        if all(v == "up" for v in state.values()) and gw[0] and pl[0]:
            say(f"Local instance: RUNNING  http://127.0.0.1:{port}/  gateway pid {st['services']['gateway']['pid']}, "
                f"pipeline pid {st['services']['pipeline']['pid']}")
            return 0
        detail = (f"gateway {state.get('gateway')}{'' if gw[0] or state.get('gateway') != 'up' else ' but ' + gw[1]}; "
                  f"pipeline {state.get('pipeline')}{'' if pl[0] or state.get('pipeline') != 'up' else ' but ' + pl[1]}")
        if st.get("status") == "RUNNING":
            st["status"] = "DEGRADED"
            st["failure"] = {"reason": detail, "at": time.time()}
            self.save(st)
        say(f"Local instance: {st.get('status')} — {detail}. Restart it with: bash run   (stop: bash run stop)")
        return 4


def _port_busy(host, port):
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False


def _tail(p, n=15):
    try:
        return "\n".join(Path(p).read_text(errors="replace").splitlines()[-n:])
    except OSError:
        return "(no log)"


def say(msg, rc=None):
    print(msg, flush=True)
    return rc


def line():
    say("-" * 60)


def main(argv):
    cmd = argv[0] if argv else "start"
    if cmd not in ("start", "stop", "status"):
        return say("Usage: bash run [start|local|stop|status]", 1)
    la = Launcher(root_dir())
    if cmd == "status":
        return la.status()
    la.lock()
    try:
        return la.start() if cmd == "start" else la.stop()
    finally:
        la.unlock()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
