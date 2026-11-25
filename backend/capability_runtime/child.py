"""Isolated handler process. Standard library only; started with `python -I` by supervisor.py.

Protocol: one JSON request on stdin, one JSON line on the ORIGINAL stdout (fd 1 is redirected to stderr while
handler code runs, so a handler cannot forge the protocol by printing)."""
import hashlib
import importlib.abc
import importlib.util
import inspect
import json
import os
import sys
import types

SKIP_DIRS = {"__pycache__"}
SKIP_SUFFIXES = (".pyc", ".pyo")
ARITY = {"execute": 2, "healthcheck": 0, "verify": 2, "reconcile": 2}


def snapshot(root):
    """Read every package file once (no symlinks) — same algorithm as capability_registry/package.py."""
    entries = {}
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        for d in list(dirnames):
            if os.path.islink(os.path.join(dirpath, d)):
                raise RuntimeError("symlinked directory in package")
        dirnames[:] = sorted(n for n in dirnames if n not in SKIP_DIRS)
        for name in sorted(filenames):
            if name.endswith(SKIP_SUFFIXES):
                continue
            full = os.path.join(dirpath, name)
            if os.path.islink(full):
                raise RuntimeError("symlink in package")
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            with open(full, "rb") as f:
                entries[rel] = f.read()
    h = hashlib.sha256()
    for rel in sorted(entries):
        h.update(rel.encode() + b"\0" + hashlib.sha256(entries[rel]).hexdigest().encode() + b"\n")
    return entries, h.hexdigest()


class SnapshotFinder(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def __init__(self, files, label, root):
        self.files, self.label, self.root = files, label, root

    def _path(self, fullname):
        base = fullname.replace(".", "/")
        if base + ".py" in self.files:
            return base + ".py", False
        if base + "/__init__.py" in self.files:
            return base + "/__init__.py", True
        return None, False

    def find_spec(self, fullname, path=None, target=None):
        rel, pkg = self._path(fullname)
        if rel is None:
            return None
        spec = importlib.util.spec_from_loader(fullname, self, is_package=pkg)
        spec.origin = f"<{self.label}>/{rel}"
        return spec

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        rel, pkg = self._path(module.__name__)
        if pkg:
            module.__path__ = []
        module.__file__ = os.path.join(self.root, rel)   # informational; code comes from the verified snapshot
        code = compile(self.files[rel], f"<{self.label}>/{rel}", "exec")
        exec(code, module.__dict__)


def limits(req):
    try:
        import resource
    except ImportError:
        return
    mem = int(req.get("memory_limit_mb", 1024)) * 1024 * 1024
    cpu = int(req.get("cpu_seconds", 60))
    for lim, val in ((resource.RLIMIT_AS, mem), (resource.RLIMIT_CPU, cpu), (resource.RLIMIT_CORE, 0),
                     (resource.RLIMIT_FSIZE, int(req.get("max_file_mb", 64)) * 1024 * 1024),
                     (resource.RLIMIT_NOFILE, 256)):
        try:
            resource.setrlimit(lim, (val, val))
        except (ValueError, OSError):
            pass


def check_arity(fn, n):
    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):
        return "signature cannot be inspected"
    params = [p for p in sig.parameters.values() if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
    has_var = any(p.kind is p.VAR_POSITIONAL for p in sig.parameters.values())
    req = [p for p in params if p.default is p.empty]
    if len(req) > n or (len(params) < n and not has_var):
        return f"must accept exactly {n} positional argument(s)"
    if inspect.iscoroutinefunction(fn):
        return "must be synchronous"
    return None


def resolve(refs, kinds):
    out, problems = {}, []
    for kind in kinds:
        ref = refs.get(kind)
        if ref is None:
            continue
        module, func = ref.split(":")
        try:
            mod = importlib.import_module(module)
        except BaseException as exc:  # noqa: BLE001
            problems.append(f"{kind} handler '{ref}' failed to import: {type(exc).__name__}: {exc}")
            continue
        fn = getattr(mod, func, None)
        if fn is None or not callable(fn):
            problems.append(f"{kind} handler '{ref}' is not a callable")
            continue
        bad = check_arity(fn, ARITY[kind])
        if bad:
            problems.append(f"{kind} handler '{ref}' {bad}")
            continue
        out[kind] = fn
    return out, problems


def main():
    proto = os.fdopen(os.dup(1), "w")
    os.dup2(2, 1)                       # handler prints go to stderr, never into the protocol stream
    req = json.loads(sys.stdin.read())
    limits(req)
    try:
        files, digest = snapshot(req["package_dir"])
    except Exception as exc:  # noqa: BLE001
        proto.write(json.dumps({"status": "integrity_failed", "error": str(exc)}) + "\n")
        return
    if digest != req["expected_digest"]:
        proto.write(json.dumps({"status": "integrity_failed", "error": "package bytes differ from the trusted digest",
                                "digest": digest}) + "\n")
        return
    sys.meta_path.insert(0, SnapshotFinder(files, "capability:" + req["capability_id"], req["package_dir"]))
    os.chdir(req.get("workdir") or os.getcwd())
    mode = req["mode"]
    kinds = list(ARITY) if mode == "probe" else [mode]
    handlers, problems = resolve(req["handlers"], kinds)
    if mode == "probe":
        for tool, kind, ref in req.get("tool_handlers") or []:
            _, extra = resolve({kind: ref}, [kind])
            problems += [f"tool '{tool}': {p}" for p in extra]
        missing = [k for k in ("execute", "healthcheck", "verify") if k not in req["handlers"]]
        proto.write(json.dumps({"status": "ok", "value": {"problems": problems + [f"missing {m}" for m in missing]}}) + "\n")
        return
    if problems:
        proto.write(json.dumps({"status": "error", "error_type": "HandlerUnavailable", "error": "; ".join(problems)}) + "\n")
        return
    fn = handlers[mode]
    ctx = types.SimpleNamespace(**(req.get("context") or {}))
    try:
        if mode == "healthcheck":
            value = fn()
        elif mode == "execute":
            value = fn(req["payload"], ctx)
        elif mode == "verify":
            value = fn(req["payload"], req["output"])
        else:  # reconcile
            value = fn(req["payload"], ctx)
        text = json.dumps({"status": "ok", "value": value})
    except BaseException as exc:  # noqa: BLE001
        text = json.dumps({"status": "error", "error_type": type(exc).__name__, "error": str(exc)[:2000]})
    proto.write(text + "\n")
    proto.flush()


if __name__ == "__main__":
    main()
