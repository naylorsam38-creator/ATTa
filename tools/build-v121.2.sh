#!/usr/bin/env bash
# Build ATTa v121.2 from the ATTa v121.1 bundle and the reviewed patch.
#
#   tools/build-v121.2.sh <ATTa-v121.1 zip> [output.zip] [--test]
#
# - Refuses any zip but the exact v121.1 bundle it was built against (SHA-256 below).
# - Applies tools/patches/v121.1-to-v121.2.patch with `git apply` (all or nothing; it never half-applies).
# - --test runs the bundle's own suite (Python + kit bats/shellcheck when installed) before zipping; a failure
#   stops the build and no zip is written.
# - Writes <output.zip> (default ./ATTa-v121.2.zip) with a single top folder ATTa-v121.2/ and prints its SHA-256.
# Nothing is deployed or restarted: the zip is uploaded the normal way (admin "Add app" / ADM / deploy kit).
set -euo pipefail
V1211_SHA256="${V1211_SHA256:-ef370fa4c6650ec2685c4b2e3094734ef7ae9995213cfc453e341ce34ef11511}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PATCH="$HERE/patches/v121.1-to-v121.2.patch"

zip_in="" out="" run_tests=0
for a in "$@"; do
  case "$a" in
    --test) run_tests=1 ;;
    -h|--help) sed -n '2,11p' "$0"; exit 0 ;;
    *) if [ -z "$zip_in" ]; then zip_in="$a"; elif [ -z "$out" ]; then out="$a"; else echo "unexpected argument: $a" >&2; exit 2; fi ;;
  esac
done
[ -n "$zip_in" ] || { echo "usage: $0 <ATTa-v121.1 zip> [output.zip] [--test]" >&2; exit 2; }
out="${out:-$PWD/ATTa-v121.2.zip}"
case "$out" in /*) ;; *) out="$PWD/$out" ;; esac
for t in unzip zip git sha256sum python3; do command -v "$t" >/dev/null || { echo "missing tool: $t" >&2; exit 3; }; done
[ -f "$PATCH" ] || { echo "patch not found: $PATCH" >&2; exit 3; }

got="$(sha256sum "$zip_in" | cut -d' ' -f1)"
[ "$got" = "$V1211_SHA256" ] || { echo "STOP: $zip_in has SHA-256 $got, not the v121.1 bundle ($V1211_SHA256)." >&2; exit 4; }
echo "[build] v121.1 bundle checksum OK"

work="$(mktemp -d)"; trap 'rm -rf "$work"' EXIT
unzip -q "$zip_in" -d "$work/in"
root="$(dirname "$(find "$work/in" -name release.json -path '*/release.json' -not -path '*/04-deployment/*' | head -1)")"
[ -f "$root/04-deployment/app_runner.py" ] || { echo "STOP: no ATTa bundle root (release.json + 04-deployment/) in the zip" >&2; exit 5; }
ver="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["version"])' "$root/release.json")"
[ "$ver" = "v121.1" ] || { echo "STOP: bundle says version $ver, expected v121.1" >&2; exit 5; }

mkdir -p "$work/out"; cp -a "$root" "$work/out/ATTa-v121.2"; dst="$work/out/ATTa-v121.2"
git -C "$dst" apply --check "$PATCH" || { echo "STOP: the patch does not apply cleanly; nothing written" >&2; exit 6; }
git -C "$dst" apply "$PATCH"
ver="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["version"])' "$dst/release.json")"
[ "$ver" = "v121.2" ] || { echo "STOP: patched bundle says $ver" >&2; exit 6; }
echo "[build] patch applied: $ver"

if [ "$run_tests" = 1 ]; then
  echo "[build] running the bundle's tests"
  ( cd "$dst" && python3 -m pytest -q -p no:cacheprovider tests ) || { echo "STOP: tests failed; no zip written" >&2; exit 7; }
  if command -v bats >/dev/null; then ( cd "$dst/05-coolify/kit" && bats tests/unit ) || { echo "STOP: kit tests failed" >&2; exit 7; }; fi
  if command -v shellcheck >/dev/null; then ( cd "$dst/05-coolify/kit" && shellcheck -x scripts/*.sh scripts/lib/*.sh ) || { echo "STOP: shellcheck failed" >&2; exit 7; }; fi
  find "$dst" -name __pycache__ -prune -exec rm -rf {} +
fi

rm -f "$out"
( cd "$work/out" && zip -qrX "$out" ATTa-v121.2 -x '*/__pycache__/*' '*.pyc' )
echo "[build] wrote $out"
echo "[build] SHA-256 $(sha256sum "$out" | cut -d' ' -f1)"
