"""Build the Stage 12 installer payload from the sidecar git history (run inside the sidecar repository).

    python installer/stage12/build_payload.py --out <dir> [--base 21104c4...]

Shared files are NOT shipped whole: code/YAML shared with other workers ship as context hunks (applied only where
the exact original context is found once), shared documents as marker-delimited sections, MASTER register as a
derived re-render. Everything else Stage 12 added ships as whole files with SHA-256."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASE = "21104c413c30a3b2714f877892970e7db19a1d19"
HUNK_FILES = ["backend/api/app.py", "backend/orchestration/gateway.py", "backend/orchestration/catalogue.py",
              "documentation/requirements/requirement_register.yaml"]
SECTION_DOCS = ["DECISIONS.md", "BLOCKERS.md", "IMPLEMENTATION_PROGRESS.md", "CHANGE_MANIFEST.md"]
DERIVED = {"MASTER_IMPLEMENTATION_REGISTER.md": {"command": ["{python}", "scripts/requirement_register.py", "render"],
                                                 "after": "documentation/requirements/requirement_register.yaml"}}
LINE_FILES = [".gitignore"]
EXCLUDE_PREFIXES = ("evidence/stage_12/runs/", "installer/stage12/")
MIGRATION = "database/migrations/0012_stage12_integrations.sql"


def git(*a: str) -> str:
    return subprocess.run(["git", "-C", str(ROOT), *a], capture_output=True, text=True, check=True).stdout


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def hunks(path: str, base: str, head: str) -> list[dict]:
    out, cur = [], None
    for line in git("diff", "-U3", base, head, "--", path).splitlines():
        if line.startswith("@@"):
            if cur:
                out.append(cur)
            cur = {"old": [], "new": []}
        elif cur is None or line.startswith(("diff ", "index ", "--- ", "+++ ")) or line.startswith("\\"):
            continue
        elif line.startswith(" "):
            cur["old"].append(line[1:])
            cur["new"].append(line[1:])
        elif line.startswith("-"):
            cur["old"].append(line[1:])
        elif line.startswith("+"):
            cur["new"].append(line[1:])
    if cur:
        out.append(cur)
    return [{"old": "\n".join(h["old"]) + "\n", "new": "\n".join(h["new"]) + "\n"} for h in out]


def section(path: str, base: str, head: str) -> str:
    head_lines = git("show", f"{head}:{path}").splitlines()
    blocks, cur, last = [], [], None
    for line in git("diff", "-U0", base, head, "--", path).splitlines():
        if line.startswith("@@"):
            if cur:
                blocks.append((last, cur))
            cur = []
            last = int(line.split("+")[1].split(",")[0].split(" ")[0])     # first new line number
        elif line.startswith("+") and not line.startswith("+++"):
            cur.append(line[1:])
    if cur:
        blocks.append((last, cur))
    text = []
    for start, lines in blocks:
        if lines and lines[0].startswith("|") and not (len(lines) > 1 and lines[1].startswith("|---")):
            for i in range(start - 2, 0, -1):          # table rows inserted mid-table: carry their header
                if head_lines[i].startswith("|---"):
                    lines = [head_lines[i - 1], head_lines[i]] + lines
                    break
        text.append("\n".join(lines).strip("\n"))
    return "\n\n".join(t for t in text if t.strip()) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--base", default=BASE)
    a = ap.parse_args()
    head = git("rev-parse", "HEAD").strip()
    out = Path(a.out)
    if out.exists():
        shutil.rmtree(out)
    (out / "files").mkdir(parents=True)
    files = []
    for line in git("diff", "--name-status", "--no-renames", a.base, head).splitlines():
        st, path = line.split("\t", 1)
        if path.startswith(EXCLUDE_PREFIXES):
            continue
        if st == "D":
            raise SystemExit(f"Stage 12 must not delete files: {path}")
        if st == "M":
            if path not in HUNK_FILES + SECTION_DOCS + list(DERIVED) + LINE_FILES:
                raise SystemExit(f"modified file without a merge strategy: {path}")
            continue
        data = subprocess.run(["git", "-C", str(ROOT), "show", f"{head}:{path}"], capture_output=True,
                              check=True).stdout
        dst = out / "files" / path
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(data)
        files.append({"path": path, "sha256": sha(data), "kind": "migration" if path == MIGRATION else "file"})
    edits = {p: hunks(p, a.base, head) for p in HUNK_FILES}
    sections = {p: section(p, a.base, head) for p in SECTION_DOCS}
    base_gi = set(git("show", f"{a.base}:.gitignore").splitlines())
    gitignore = [ln for ln in git("show", f"{head}:.gitignore").splitlines() if ln not in base_gi]
    short = head[:7]
    manifest = {"name": "atta-stage12-integrations", "version": f"12.1.0+{short}", "base_commit": a.base,
                "built_from": head, "files": files, "edits": edits, "sections": sections,
                "gitignore": gitignore, "derived": DERIVED, "migration": MIGRATION,
                "requires": ["backend/orchestration/gateway.py", "backend/orchestration/catalogue.py",
                             "backend/orchestration/controller.py", "backend/capability_registry/registry.py",
                             "backend/api/app.py", "database/migrations/0002_runtime_controller.sql"]}
    mtext = json.dumps(manifest, indent=1, sort_keys=True)
    (out / "manifest.json").write_text(mtext)
    sums = [f"{sha(mtext.encode())}  manifest.json"] + [f"{f['sha256']}  files/{f['path']}" for f in files]
    (out / "SHA256SUMS.txt").write_text("\n".join(sums) + "\n")
    print(json.dumps({"payload": str(out), "version": manifest["version"], "files": len(files),
                      "hunks": {k: len(v) for k, v in edits.items()}, "sections": list(sections)}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
