#!/usr/bin/env python3
"""Run the record-driven diagnostic read-only against a completed ATTa state tree."""
from __future__ import annotations
import argparse, json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
import record_diagnostic as rd

APPS = [
    "apache-airflow","appsmith","billionmail","botpress","clearflask","colanode",
    "coolify","flarum","ghost","krayin-crm","langflow","medplum","memos",
    "open-design","opencart","opencloud","plane","polar","supabase",
    "super-productivity","traefik","umami",
]

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--records-root", required=True,
                    help="ATTa state/runner/failures directory")
    ap.add_argument("--output", required=True,
                    help="report path outside records-root")
    args = ap.parse_args()
    root = Path(args.records_root).resolve()
    outpath = Path(args.output).resolve()
    if outpath.is_relative_to(root):
        raise SystemExit("Refusing to write diagnostic output inside the input evidence tree")
    result = rd.run(root, APPS)
    result["input_root"] = str(root)
    result["output_path"] = str(outpath)
    outpath.parent.mkdir(parents=True, exist_ok=True)
    outpath.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({
        "processed_count": result["processed_count"],
        "expected_count": result["expected_count"],
        "missing_apps": result["missing_apps"],
        "output": str(outpath),
        "read_only": True,
    }, indent=2))
    return 0 if not result["missing_apps"] and result["processed_count"] == 22 else 2

if __name__ == "__main__":
    raise SystemExit(main())
