#!/usr/bin/env python3
"""ATTa Stage 12 (connectors, MCP, skills, plugins) — transactional plug-in installer. Standard library only;
Windows and Linux (Python 3.10+).

    python install.py --target <ATTa checkout> --dry-run          # plan + conflicts, changes nothing
    python install.py --target <ATTa checkout>                    # install (or upgrade) transactionally
    python install.py --target <ATTa checkout> --uninstall        # remove exactly what was installed
    python install.py --target <ATTa checkout> --recover          # finish/undo an interrupted transaction
    options: --migration-number N|auto   --payload DIR   --python PATH   --no-verify-import   --json

Guarantees
  * payload checksums are verified before anything is planned (SHA256SUMS.txt + manifest.json);
  * existing working functionality is never overwritten: a new file whose path already exists with different
    content, a shared-code hunk whose exact original context is not found exactly once, or a migration number used by
    another worker is a CONFLICT and NOTHING is changed;
  * shared code (gateway.py, catalogue.py, app.py, requirement register) is patched hunk by hunk, so other workers'
    additions elsewhere in those files are preserved; shared documents get a marker-delimited Stage 12 section;
  * every change is journalled with backups first; any failure (including the post-install verification) rolls the
    target back byte-for-byte; an interrupted run is recovered with --recover (or automatically on the next run);
  * repeat installation is a no-op; a newer payload upgrades only files still identical to what was installed;
  * CRLF (Windows) checkouts keep their line endings.
Exit codes: 0 ok / no-op · 2 bad payload · 3 conflicts (nothing changed) · 4 failed and rolled back · 5 bad target."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
STATE_DIR = ".atta-stage12"
BEGIN = "<!-- ATTA-STAGE12:BEGIN {doc} -->"
END = "<!-- ATTA-STAGE12:END {doc} -->"
GI_BEGIN, GI_END = "# >>> ATTa Stage 12 >>>", "# <<< ATTa Stage 12 <<<"


class PayloadError(Exception):
    pass


class Conflict(Exception):
    pass


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


# ---------------------------------------------------------------------------------------------------- text helpers
def read_text(p: Path) -> tuple[str, str]:
    """Return (LF-normalised text, newline style) for a text file."""
    raw = p.read_bytes().decode("utf-8")
    nl = "\r\n" if "\r\n" in raw else "\n"
    return raw.replace("\r\n", "\n"), nl


def encode(text: str, nl: str) -> bytes:
    return (text.replace("\n", "\r\n") if nl == "\r\n" else text).encode("utf-8")


def section_block(doc: str, body: str) -> str:
    return f"{BEGIN.format(doc=doc)}\n{body.rstrip()}\n{END.format(doc=doc)}\n"


def find_section(text: str, doc: str) -> tuple[int, int] | None:
    b, e = BEGIN.format(doc=doc), END.format(doc=doc)
    i = text.find(b)
    if i < 0:
        return None
    j = text.find(e, i)
    if j < 0:
        raise Conflict(f"{doc}: Stage 12 begin marker without end marker (edited by hand?)")
    return i, j + len(e) + (1 if text[j + len(e): j + len(e) + 1] == "\n" else 0)


# ---------------------------------------------------------------------------------------------------- payload
def load_payload(payload: Path) -> dict:
    sums = payload / "SHA256SUMS.txt"
    if not sums.is_file() or not (payload / "manifest.json").is_file():
        raise PayloadError(f"{payload}: SHA256SUMS.txt / manifest.json missing")
    listed = {}
    for line in sums.read_text().splitlines():
        if line.strip():
            h, rel = line.split("  ", 1)
            listed[rel] = h
    bad = [rel for rel, h in listed.items() if not (payload / rel).is_file() or sha((payload / rel).read_bytes()) != h]
    if bad:
        raise PayloadError(f"payload checksum mismatch / missing: {bad[:10]}")
    m = json.loads((payload / "manifest.json").read_text())
    for f in m["files"]:
        if listed.get(f"files/{f['path']}") != f["sha256"]:
            raise PayloadError(f"file {f['path']} is not covered by SHA256SUMS.txt")
        if f["path"].startswith(("/", "\\")) or ".." in Path(f["path"]).parts or ":" in f["path"]:
            raise PayloadError(f"unsafe path in payload: {f['path']}")
    return m


# ---------------------------------------------------------------------------------------------------- planning
MIG_RE = re.compile(r"^(\d{4})_.*\.sql$")


def migration_target(target: Path, m: dict, choice: str | None, record: dict | None) -> tuple[str, list[str]]:
    """Return (target relative path for our migration, problems)."""
    ours = m["migration"]
    name = Path(ours).name
    mdir = target / "database" / "migrations"
    others = {}
    if mdir.is_dir():
        for p in mdir.glob("*.sql"):
            mm = MIG_RE.match(p.name)
            if mm and p.name != name and not (record and record.get("migration") == f"database/migrations/{p.name}"):
                others.setdefault(mm.group(1), []).append(p.name)
    if record and record.get("migration"):
        installed = record["migration"]
        if (target / installed).is_file():
            return installed, []
    num = MIG_RE.match(name).group(1)
    if choice is None:
        if num in others:
            return ours, [f"migration number {num} already used by {others[num]} (another worker). Choose a free "
                          f"number with --migration-number N or --migration-number auto; never renumber a "
                          f"migration that a database has already applied"]
        return ours, []
    if choice == "auto":
        used = {int(k) for k in others} | {2}
        n = max(used | {int(num) - 1}) + 1 if int(num) in used else int(num)
    else:
        n = int(choice)
        if f"{n:04d}" in others:
            return ours, [f"--migration-number {n:04d} is already used by {others[f'{n:04d}']}"]
    if n <= 2:
        return ours, ["the Stage 12 migration must sort after 0002_runtime_controller.sql"]
    return f"database/migrations/{n:04d}{name[4:]}", []


def reverse_stale(text: str, rel: str, record: dict | None, m: dict) -> str:
    """Upgrade: undo (in memory) the previously installed hunks that the new version no longer contains."""
    new_set = {h["new"] for h in m["edits"].get(rel, [])}
    for h in ((record or {}).get("edits", {}).get(rel) or []):
        if h["new"] in new_set or h["new"] == h["old"]:
            continue
        if text.count(h["new"]) != 1:
            raise Conflict(f"{rel}: a hunk installed by {record['version']} is no longer intact (changed by someone "
                           f"else) — not touched")
        text = text.replace(h["new"], h["old"], 1)
    return text


def ledger_conflicts(target: Path, python: str | None, mig_path: str) -> list[str]:
    """Optional (--check-db): migrations already APPLIED in the target's database with our number but another name."""
    code = ("import json; from backend.settings import load_settings; import psycopg\n"
            "s=load_settings(); c=psycopg.connect(s.database.conninfo(read_only=True))\n"
            "ex=c.execute(\"SELECT to_regclass('public.atta_schema_migrations')\").fetchone()[0]\n"
            "print(json.dumps([r[0] for r in c.execute('SELECT name FROM public.atta_schema_migrations')] if ex else []))")
    r = subprocess.run([python or sys.executable, "-c", code], cwd=target, capture_output=True, text=True,
                       timeout=60, env={**os.environ, "PYTHONPATH": str(target)})
    if r.returncode != 0:
        return [f"--check-db: database ledger not readable ({(r.stderr or r.stdout).strip()[-200:]})"]
    return compare_ledger(json.loads(r.stdout.strip().splitlines()[-1]), mig_path)


def compare_ledger(applied: list[str], mig_path: str) -> list[str]:
    name = Path(mig_path).name
    num = name[:4]
    out = [f"database already applied {a} with migration number {num}" for a in applied
           if a.startswith(f"migration:{num}_") and a != f"migration:{name}"]
    ours = [a for a in applied if a.startswith("migration:") and a[len("migration:") + 4:] == name[4:] and
            a != f"migration:{name}"]
    out += [f"database already applied the Stage 12 migration as {a}; install it under that same name "
            f"(--migration-number {a[len('migration:'):][:4]})" for a in ours]
    return out


def plan(target: Path, payload: Path, m: dict, record: dict | None, mig_choice: str | None) -> dict:
    ops, conflicts, notes = [], [], []
    missing = [r for r in m["requires"] if not (target / r).is_file()]
    if missing:
        raise Conflict(f"target is not an ATTa Stage 01A tree (missing {missing})")
    prev_files = {f["path"]: f for f in (record or {}).get("files", [])}
    mig_path, mig_problems = migration_target(target, m, mig_choice, record)
    conflicts += mig_problems
    for f in m["files"]:
        rel = mig_path if f["kind"] == "migration" else f["path"]
        dst = target / rel
        if not dst.exists():
            ops.append({"op": "create", "path": rel, "src": f"files/{f['path']}", "sha256": f["sha256"]})
            continue
        if dst.is_dir() or dst.is_symlink():
            conflicts.append(f"{rel}: exists as a directory/symlink")
            continue
        cur = sha(dst.read_bytes())
        if cur == f["sha256"]:
            ops.append({"op": "same", "path": rel})
        elif rel in prev_files and prev_files[rel]["sha256"] == cur:
            ops.append({"op": "update", "path": rel, "src": f"files/{f['path']}", "sha256": f["sha256"]})
        else:
            who = "modified after the previous Stage 12 install" if rel in prev_files else \
                "exists with different content (not installed by Stage 12)"
            conflicts.append(f"{rel}: {who} — not overwritten")
    for rel, hunks in m["edits"].items():
        p = target / rel
        if not p.is_file():
            conflicts.append(f"{rel}: shared file missing")
            continue
        text, _nl = read_text(p)
        try:
            text = reverse_stale(text, rel, record, m)
        except Conflict as exc:
            conflicts.append(str(exc))
            continue
        for i, h in enumerate(hunks):
            # Upgrade: hunks of the previously installed version are reversed first (planned separately below).
            n_new, n_old = text.count(h["new"]), text.count(h["old"])
            if h["new"] != h["old"] and n_new == 1:
                ops.append({"op": "hunk-present", "path": rel, "hunk": i})
            elif n_old == 1:
                ops.append({"op": "hunk", "path": rel, "hunk": i})
            else:
                conflicts.append(f"{rel}: hunk {i + 1}/{len(hunks)} — the original context was found {n_old} times "
                                 f"(expected exactly 1); another change overlaps this location. First lines:\n      "
                                 + "\n      ".join(h["old"].splitlines()[:4]))
    for doc, body in m["sections"].items():
        p = target / doc
        if not p.is_file():
            ops.append({"op": "section-create", "path": doc})
            continue
        text, _ = read_text(p)
        loc = find_section(text, doc)
        if loc is None:
            ops.append({"op": "section-append", "path": doc})
        elif text[loc[0]:loc[1]] == section_block(doc, body):
            ops.append({"op": "section-present", "path": doc})
        else:
            ops.append({"op": "section-replace", "path": doc})
    gi = target / ".gitignore"
    gi_text = read_text(gi)[0] if gi.is_file() else ""
    want = [ln for ln in m["gitignore"] if ln not in gi_text.splitlines()]
    ops.append({"op": "gitignore" if want else "gitignore-present", "path": ".gitignore", "lines": want})
    for rel, d in m["derived"].items():
        script = target / d["command"][1]
        if script.is_file():
            ops.append({"op": "derived", "path": rel, "command": d["command"]})
        else:
            notes.append(f"{rel}: generator {d['command'][1]} not present — left unchanged")
    return {"ops": ops, "conflicts": conflicts, "notes": notes, "migration": mig_path}


# ---------------------------------------------------------------------------------------------------- transaction
class Txn:
    def __init__(self, target: Path, label: str):
        self.target = target
        self.dir = target / STATE_DIR / f"txn-{time.strftime('%Y%m%dT%H%M%S')}-{os.getpid()}"
        self.dir.mkdir(parents=True)
        self.journal = {"label": label, "state": "applying", "backups": {}, "created": [], "created_dirs": []}
        self._save()

    def _save(self):
        (self.dir / "journal.json").write_text(json.dumps(self.journal, indent=1))

    def touch(self, rel: str) -> None:
        p = self.target / rel
        if rel in self.journal["backups"] or rel in self.journal["created"]:
            return
        if p.exists():
            b = self.dir / "backup" / rel
            b.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, b)
            self.journal["backups"][rel] = sha(p.read_bytes())
        else:
            d, new_dirs = p.parent, []
            while not d.exists():
                new_dirs.append(str(d.relative_to(self.target)))
                d = d.parent
            self.journal["created_dirs"] += new_dirs
            self.journal["created"].append(rel)
        self._save()

    def write(self, rel: str, data: bytes) -> None:
        self.touch(rel)
        p = self.target / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + ".stage12-tmp")
        tmp.write_bytes(data)
        os.replace(tmp, p)
        if sha(p.read_bytes()) != sha(data):
            raise IOError(f"write verification failed for {rel}")

    def delete(self, rel: str) -> None:
        self.touch(rel)
        (self.target / rel).unlink()

    def commit(self):
        self.journal["state"] = "committed"
        self._save()

    def rollback(self):
        restore(self.target, self.dir)


def restore(target: Path, txn_dir: Path) -> list[str]:
    j = json.loads((txn_dir / "journal.json").read_text())
    done = []
    for rel in j["created"]:
        p = target / rel
        if p.exists():
            p.unlink()
            done.append(f"removed {rel}")
    for rel in j["backups"]:
        b = txn_dir / "backup" / rel
        (target / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(b, target / rel)
        done.append(f"restored {rel}")
    for d in sorted(set(j["created_dirs"]), key=lambda x: -len(x)):
        try:
            (target / d).rmdir()
        except OSError:
            pass
    for t in target.rglob("*.stage12-tmp"):
        t.unlink()
    j["state"] = "rolled_back"
    (txn_dir / "journal.json").write_text(json.dumps(j, indent=1))
    bad = [rel for rel, h in j["backups"].items() if sha((target / rel).read_bytes()) != h]
    if bad:
        raise IOError(f"rollback could not restore {bad}")
    return done


def recover(target: Path) -> list[str]:
    out = []
    sd = target / STATE_DIR
    for jf in sorted(sd.glob("txn-*/journal.json")) if sd.is_dir() else []:
        if json.loads(jf.read_text())["state"] == "applying":
            out += restore(target, jf.parent)
            out.append(f"recovered interrupted transaction {jf.parent.name}")
    return out


def fault(step: int) -> None:
    """Test hooks: FAIL_AT raises (normal rollback); CRASH_AT kills the process (recovery on the next run)."""
    at = os.environ.get("ATTA_STAGE12_INSTALLER_FAIL_AT")
    if at is not None and int(at) == step:
        raise RuntimeError(f"injected failure at step {step} (test)")
    crash = os.environ.get("ATTA_STAGE12_INSTALLER_CRASH_AT")
    if crash is not None and int(crash) == step:
        os._exit(9)


# ---------------------------------------------------------------------------------------------------- apply
def apply(target: Path, payload: Path, m: dict, pl: dict, record: dict | None, python: str | None,
          verify_import: bool) -> dict:
    txn = Txn(target, f"install {m['version']}")
    step = 0
    try:
        # upgrade: reverse the previous version's hunks that differ from this version's
        prev_edits = (record or {}).get("edits", {})
        for rel, hunks in prev_edits.items():
            new_set = {h["new"] for h in m["edits"].get(rel, [])}
            stale = [h for h in hunks if h["new"] not in new_set and h["new"] != h["old"]]
            if stale:
                text, nl = read_text(target / rel)
                for h in stale:
                    if text.count(h["new"]) != 1:
                        raise Conflict(f"{rel}: previously installed hunk no longer found intact")
                    text = text.replace(h["new"], h["old"], 1)
                txn.write(rel, encode(text, nl))
        by_file: dict[str, list] = {}
        for op in pl["ops"]:
            by_file.setdefault(op["path"], []).append(op)
        for op in pl["ops"]:
            step += 1
            fault(step)
            k, rel = op["op"], op["path"]
            if k in ("create", "update"):
                txn.write(rel, (payload / op["src"]).read_bytes())
            elif k == "hunk":
                text, nl = read_text(target / rel)
                h = m["edits"][rel][op["hunk"]]
                if text.count(h["old"]) != 1:
                    raise Conflict(f"{rel}: hunk {op['hunk'] + 1} context changed during installation")
                txn.write(rel, encode(text.replace(h["old"], h["new"], 1), nl))
            elif k in ("section-append", "section-replace", "section-create"):
                p = target / rel
                text, nl = read_text(p) if p.is_file() else ("", "\n")
                block = section_block(rel, m["sections"][rel])
                loc = find_section(text, rel) if text else None
                if loc:
                    text = text[:loc[0]] + block + text[loc[1]:]
                else:
                    text = text.rstrip("\n") + ("\n\n" if text else "") + block
                txn.write(rel, encode(text, nl))
            elif k == "gitignore":
                p = target / rel
                text, nl = read_text(p) if p.is_file() else ("", "\n")
                text = text.rstrip("\n") + f"\n{GI_BEGIN}\n" + "\n".join(op["lines"]) + f"\n{GI_END}\n"
                txn.write(rel, encode(text, nl))
            elif k == "derived":
                txn.touch(rel)
                cmd = [python or sys.executable if c == "{python}" else c for c in op["command"]]
                r = subprocess.run(cmd, cwd=target, capture_output=True, text=True, timeout=120)
                if r.returncode != 0:
                    raise RuntimeError(f"{rel}: generator failed: {(r.stdout + r.stderr)[-500:]}")
        # post-install verification (nothing is committed unless it passes)
        problems = verify(target, payload, m, pl)
        if verify_import and python:
            r = subprocess.run([python, "-c", "import backend.integrations.service, backend.integrations.api, "
                                "backend.orchestration.gateway, backend.orchestration.catalogue, backend.api.app"],
                               cwd=target, capture_output=True, text=True, timeout=300,
                               env={**os.environ, "PYTHONPATH": str(target)})
            if r.returncode != 0:
                problems.append(f"import check failed: {(r.stdout + r.stderr)[-800:]}")
        if problems:
            raise RuntimeError("post-install verification failed: " + "; ".join(problems))
        txn.commit()
    except BaseException as exc:
        done = txn.rollback()
        return {"status": "ROLLED_BACK", "error": f"{type(exc).__name__}: {exc}", "restored": done}
    files = []
    for f in m["files"]:
        rel = pl["migration"] if f["kind"] == "migration" else f["path"]
        files.append({"path": rel, "sha256": f["sha256"]})
    rec = {"name": m["name"], "version": m["version"], "built_from": m["built_from"], "installed_at":
           time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "files": files, "edits": m["edits"],
           "sections": sorted(m["sections"]), "gitignore": m["gitignore"], "migration": pl["migration"],
           "derived": m["derived"],
           "transaction": txn.dir.name, "payload_manifest_sha256": sha((payload / "manifest.json").read_bytes())}
    (target / STATE_DIR / "installed.json").write_text(json.dumps(rec, indent=1))
    return {"status": "INSTALLED", "version": m["version"], "migration": pl["migration"], "transaction": txn.dir.name}


def verify(target: Path, payload: Path, m: dict, pl: dict) -> list[str]:
    problems = []
    for f in m["files"]:
        rel = pl["migration"] if f["kind"] == "migration" else f["path"]
        p = target / rel
        if not p.is_file() or sha(p.read_bytes()) != f["sha256"]:
            problems.append(f"{rel}: checksum mismatch after install")
    for rel, hunks in m["edits"].items():
        text, _ = read_text(target / rel)
        for i, h in enumerate(hunks):
            if text.count(h["new"]) != 1:
                problems.append(f"{rel}: hunk {i + 1} not present exactly once after install")
    for doc, body in m["sections"].items():
        text, _ = read_text(target / doc)
        loc = find_section(text, doc)
        if loc is None or text[loc[0]:loc[1]] != section_block(doc, body):
            problems.append(f"{doc}: Stage 12 section missing after install")
    return problems


# ---------------------------------------------------------------------------------------------------- uninstall
def uninstall(target: Path, record: dict, python: str, derived: dict | None = None) -> dict:
    conflicts = []
    for f in record["files"]:
        p = target / f["path"]
        if p.is_file() and sha(p.read_bytes()) != f["sha256"]:
            conflicts.append(f"{f['path']}: modified since installation — not removed")
    texts = {}
    for rel, hunks in record["edits"].items():
        text, nl = read_text(target / rel)
        for i, h in enumerate(hunks):
            if h["new"] == h["old"]:
                continue
            if text.count(h["new"]) != 1:
                conflicts.append(f"{rel}: installed hunk {i + 1} no longer found intact")
            else:
                text = text.replace(h["new"], h["old"], 1)
        texts[rel] = (text, nl)
    if conflicts:
        return {"status": "CONFLICTS", "conflicts": conflicts}
    txn = Txn(target, f"uninstall {record['version']}")
    try:
        for f in record["files"]:
            if (target / f["path"]).is_file():
                txn.delete(f["path"])
        for rel, (text, nl) in texts.items():
            txn.write(rel, encode(text, nl))
        for doc in record["sections"]:
            p = target / doc
            if p.is_file():
                text, nl = read_text(p)
                loc = find_section(text, doc)
                if loc:
                    new = (text[:loc[0]].rstrip("\n") + "\n" + text[loc[1]:]).rstrip("\n") + "\n"
                    txn.write(doc, encode(new, nl))
        gi = target / ".gitignore"
        if gi.is_file():
            text, nl = read_text(gi)
            i, j = text.find(GI_BEGIN), text.find(GI_END)
            if i >= 0 and j > i:
                txn.write(".gitignore", encode((text[:i].rstrip("\n") + "\n" + text[j + len(GI_END):].lstrip("\n"))
                                               .rstrip("\n") + "\n", nl))
        for d in sorted({str(Path(f["path"]).parent) for f in record["files"]}, key=lambda x: -len(x)):
            dd = target / d
            while dd != target and dd.is_dir() and not any(dd.iterdir()):
                dd.rmdir()
                dd = dd.parent
        for rel, d in (derived or {}).items():
            if (target / d["command"][1]).is_file():
                txn.touch(rel)
                cmd = [python if c == "{python}" else c for c in d["command"]]
                r = subprocess.run(cmd, cwd=target, capture_output=True, text=True, timeout=120)
                if r.returncode != 0:
                    raise RuntimeError(f"{rel}: generator failed: {(r.stdout + r.stderr)[-500:]}")
        txn.commit()
    except BaseException as exc:
        return {"status": "ROLLED_BACK", "error": f"{type(exc).__name__}: {exc}", "restored": txn.rollback()}
    (target / STATE_DIR / "installed.json").unlink()
    return {"status": "UNINSTALLED", "version": record["version"],
            "note": "if a database already applied the Stage 12 migration, its `integrations` schema and audit history "
                    "are kept (never dropped by the installer)"}


# ---------------------------------------------------------------------------------------------------- main
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--target", default=".")
    ap.add_argument("--payload", default=str(HERE / "payload"))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--uninstall", action="store_true")
    ap.add_argument("--recover", action="store_true")
    ap.add_argument("--migration-number")
    ap.add_argument("--python", help="interpreter of the target (default: <target>/.venv, else this one)")
    ap.add_argument("--no-verify-import", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--check-db", action="store_true",
                    help="also refuse when the target database already applied a migration with our number")
    a = ap.parse_args(argv)
    target = Path(a.target).resolve()
    out = {"target": str(target)}

    def done(code: int) -> int:
        out["exit_code"] = code
        print(json.dumps(out, indent=1) if a.json else "\n".join(f"{k}: {v}" for k, v in out.items()))
        return code

    if not (target / "backend").is_dir():
        out["error"] = "target is not an ATTa checkout"
        return done(5)
    python = a.python
    if python is None:
        for c in (target / ".venv" / "bin" / "python", target / ".venv" / "Scripts" / "python.exe"):
            if c.is_file():
                python = str(c)
                break
    python = python or sys.executable
    rec_path = target / STATE_DIR / "installed.json"
    recovered = recover(target)
    if recovered:
        out["recovered"] = recovered
    if a.recover:
        return done(0)
    record = json.loads(rec_path.read_text()) if rec_path.is_file() else None
    if a.uninstall:
        if record is None:
            out["status"] = "NOT_INSTALLED"
            return done(0)
        out.update(uninstall(target, record, python, record.get("derived")))
        return done({"UNINSTALLED": 0, "CONFLICTS": 3}.get(out["status"], 4))
    try:
        m = load_payload(Path(a.payload))
    except PayloadError as exc:
        out["error"] = str(exc)
        return done(2)
    out["version"] = m["version"]
    if record and record["version"] == m["version"] and record.get("payload_manifest_sha256") == \
            sha((Path(a.payload) / "manifest.json").read_bytes()):
        try:
            pl0 = plan(target, Path(a.payload), m, record, a.migration_number)
            problems = verify(target, Path(a.payload), m, pl0)
        except Conflict as exc:
            problems = [str(exc)]
        out["status"] = "ALREADY_INSTALLED" if not problems else "INSTALLED_BUT_ALTERED"
        if problems:
            out["problems"] = problems
        return done(0 if not problems else 3)
    try:
        pl = plan(target, Path(a.payload), m, record, a.migration_number)
    except Conflict as exc:
        out.update(status="CONFLICTS", conflicts=[str(exc)])
        return done(3 if (target / "backend").is_dir() else 5)
    out["mode"] = "upgrade from " + record["version"] if record else "install"
    out["plan"] = {k: sum(1 for o in pl["ops"] if o["op"] == k) for k in sorted({o["op"] for o in pl["ops"]})}
    out["migration"] = pl["migration"]
    if pl["notes"]:
        out["notes"] = pl["notes"]
    if a.check_db:
        pl["conflicts"] += ledger_conflicts(target, python, pl["migration"])
    if pl["conflicts"]:
        out.update(status="CONFLICTS", conflicts=pl["conflicts"])
        return done(3)
    if a.dry_run:
        out["status"] = "DRY_RUN_OK"
        out["changes"] = [f"{o['op']}: {o['path']}" + (f" #{o['hunk'] + 1}" if "hunk" in o else "")
                          for o in pl["ops"] if not o["op"].endswith(("present", "same"))]
        return done(0)
    out.update(apply(target, Path(a.payload), m, pl, record, python, not a.no_verify_import))
    return done(0 if out["status"] == "INSTALLED" else 4)


if __name__ == "__main__":
    sys.exit(main())
