"""Model discovery (Stage 01B §5). Bounded and incremental: only configured roots, Windows candidate roots that
exist, removable media (if enabled) and known runtime stores, to a fixed depth; unchanged files are revalidated
from the index by size+mtime. Models are referenced where they are — never copied, never downloaded."""
from __future__ import annotations

import hashlib
import json
import os
import struct
from dataclasses import asdict, dataclass, field
from pathlib import Path

GGUF_MAGIC = b"GGUF"
SKIP_DIRS = {"$RECYCLE.BIN", "System Volume Information", "Windows", "Program Files", "Program Files (x86)", "proc",
             "sys", "dev", "node_modules", ".git", "__pycache__"}


@dataclass
class ModelRecord:
    model_id: str
    display_name: str
    path: str
    format: str                       # gguf | safetensors | ollama
    size_bytes: int
    fingerprint: str                  # sha256 over size + first/last MiB (stable, cheap); full hash on demand
    compatible_runtimes: list[str]
    estimated_memory_mb: int
    context_length: int | None
    architecture: str | None
    status: str = "AVAILABLE"
    last_verified: str = ""
    mtime_ns: int = 0
    removable: bool = False
    extra: dict = field(default_factory=dict)


def fingerprint(path: Path) -> str:
    size = path.stat().st_size
    h = hashlib.sha256(str(size).encode())
    with path.open("rb") as f:
        h.update(f.read(1 << 20))
        if size > 2 << 20:
            f.seek(-(1 << 20), os.SEEK_END)
            h.update(f.read(1 << 20))
    return "sha256-partial:" + h.hexdigest()


def full_checksum(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 22), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


# ------------------------------------------------------------------------------------------------ GGUF metadata
_SCALAR = {0: "<B", 1: "<b", 2: "<H", 3: "<h", 4: "<I", 5: "<i", 6: "<f", 7: "<?", 10: "<Q", 11: "<q", 12: "<d"}


def _read_str(f) -> str:
    (n,) = struct.unpack("<Q", f.read(8))
    if n > 1 << 20:
        raise ValueError("implausible GGUF string length")
    return f.read(n).decode("utf-8", "replace")


def _read_value(f, vtype: int, keep: bool):
    if vtype in _SCALAR:
        fmt = _SCALAR[vtype]
        return struct.unpack(fmt, f.read(struct.calcsize(fmt)))[0]
    if vtype == 8:
        return _read_str(f)
    if vtype == 9:
        (itype,) = struct.unpack("<I", f.read(4))
        (count,) = struct.unpack("<Q", f.read(8))
        if itype in _SCALAR:          # arrays (e.g. tokenizer tables) are skipped, never loaded
            f.seek(struct.calcsize(_SCALAR[itype]) * count, os.SEEK_CUR)
            return None
        for _ in range(count):
            _read_value(f, itype, False)
        return None
    raise ValueError(f"unknown GGUF value type {vtype}")


def gguf_metadata(path: Path, max_kv: int = 512) -> dict:
    with path.open("rb") as f:
        if f.read(4) != GGUF_MAGIC:
            raise ValueError("not a GGUF file")
        (version,) = struct.unpack("<I", f.read(4))
        _tensors, kv = struct.unpack("<QQ", f.read(16))
        meta = {"gguf_version": version}
        for _ in range(min(kv, max_kv)):
            key = _read_str(f)
            (vtype,) = struct.unpack("<I", f.read(4))
            wanted = key in ("general.name", "general.architecture", "general.file_type") or key.endswith(".context_length")
            val = _read_value(f, vtype, keep=False)
            if wanted:
                meta[key] = val
        arch = meta.get("general.architecture")
        meta["context_length"] = meta.get(f"{arch}.context_length") if arch else None
        return meta


# ------------------------------------------------------------------------------------------------ discovery
def candidate_roots(settings, storage: list[dict] | None = None) -> list[tuple[Path, bool]]:
    roots: list[tuple[Path, bool]] = [(Path(p), False) for p in settings.models.storage_paths]
    if os.name == "nt":
        roots += [(Path(p), False) for p in settings.deployment.windows_candidate_roots if Path(p).is_dir()]
    if settings.deployment.scan_removable_media:
        roots += [(Path(s["mountpoint"]), True) for s in storage or [] if s.get("removable")]
    ollama = Path(os.environ.get("OLLAMA_MODELS") or Path.home() / ".ollama" / "models")
    if ollama.is_dir():
        roots.append((ollama, False))
    seen, out = set(), []
    for r, rem in roots:
        key = str(r.resolve()) if r.exists() else str(r)
        if key not in seen:
            seen.add(key)
            out.append((r, rem))
    return out


def _walk(root: Path, depth: int):
    if not root.is_dir():
        return
    stack = [(root, 0)]
    while stack:
        d, lvl = stack.pop()
        try:
            entries = list(os.scandir(d))
        except (PermissionError, OSError):
            continue
        yield d, entries
        if lvl < depth:
            for e in entries:
                if e.is_dir(follow_symlinks=False) and e.name not in SKIP_DIRS and not e.name.startswith("."):
                    stack.append((Path(e.path), lvl + 1))


def discover_models(settings, storage: list[dict] | None, index_path: Path | None) -> list[ModelRecord]:
    from backend.contracts import iso_utc
    index = {}
    if index_path and index_path.is_file():
        try:
            index = json.loads(index_path.read_text())
        except ValueError:
            index = {}
    found: dict[str, ModelRecord] = {}
    for root, removable in candidate_roots(settings, storage):
        for d, entries in _walk(root, settings.deployment.model_scan_depth):
            names = {e.name for e in entries}
            # Hugging Face style directory (vLLM): config.json + *.safetensors
            st = [e for e in entries if e.name.endswith(".safetensors") and e.is_file()]
            if st and "config.json" in names:
                size = sum(e.stat().st_size for e in st)
                ctx = arch = None
                try:
                    cfg = json.loads((d / "config.json").read_text())
                    ctx = cfg.get("max_position_embeddings")
                    arch = (cfg.get("architectures") or [None])[0]
                except (OSError, ValueError):
                    pass
                fp = fingerprint(Path(st[0].path))
                found[str(d)] = ModelRecord(d.name, d.name, str(d), "safetensors", size, fp, ["vllm"],
                                            int(size / 2**20 * 1.2) + 1024, ctx, arch, last_verified=iso_utc(),
                                            removable=removable)
            if d.name == "manifests" and (d.parent / "blobs").is_dir():
                for mf in d.rglob("*"):
                    if mf.is_file():
                        try:
                            doc = json.loads(mf.read_text())
                        except (OSError, ValueError):
                            continue
                        layers = doc.get("layers") or []
                        size = sum(int(x.get("size", 0)) for x in layers)
                        rel = mf.relative_to(d).parts
                        mid = ":".join(rel[-2:]) if len(rel) >= 2 else mf.name
                        found[str(mf)] = ModelRecord(mid, mid, str(mf), "ollama", size,
                                                     "sha256-manifest:" + hashlib.sha256(mf.read_bytes()).hexdigest(),
                                                     ["ollama"], int(size / 2**20 * 1.2) + 512, None, None,
                                                     last_verified=iso_utc(), removable=removable)
            for e in entries:
                if not (e.is_file() and e.name.lower().endswith(".gguf")):
                    continue
                p = Path(e.path)
                stt = e.stat()
                cached = index.get(str(p))
                if cached and cached.get("size_bytes") == stt.st_size and cached.get("mtime_ns") == stt.st_mtime_ns:
                    rec = ModelRecord(**cached)       # unchanged: incremental revalidation from the index
                    rec.removable = removable
                    found[str(p)] = rec
                    continue
                try:
                    meta = gguf_metadata(p)
                    status = "AVAILABLE"
                except (ValueError, struct.error, OSError) as exc:
                    meta, status = {"error": str(exc)}, "INVALID"
                name = meta.get("general.name") or p.stem
                found[str(p)] = ModelRecord(p.stem, str(name), str(p), "gguf", stt.st_size, fingerprint(p),
                                            ["llamacpp", "ollama"] if status == "AVAILABLE" else [],
                                            int(stt.st_size / 2**20 * 1.1) + 256, meta.get("context_length"),
                                            meta.get("general.architecture"), status=status, last_verified=iso_utc(),
                                            mtime_ns=stt.st_mtime_ns, removable=removable,
                                            extra={k: v for k, v in meta.items() if k in ("gguf_version", "error")})
    models = sorted(found.values(), key=lambda m: (m.model_id, m.path))
    if index_path:
        index_path.parent.mkdir(parents=True, exist_ok=True)
        index_path.write_text(json.dumps({m.path: asdict(m) for m in models}, indent=2))
    return models


def find(models: list[ModelRecord], model_id: str) -> ModelRecord | None:
    return next((m for m in models if m.model_id == model_id or m.display_name == model_id), None)
