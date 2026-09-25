#!/usr/bin/env bash
# snapshot.sh LABEL — copy ATTa's own records (read-only) out of the testbed into evidence/LABEL/.
# Nothing here writes to ATTa; secrets (.env values, tokens, users.json, TEST_ACCOUNTS) are never copied.
set -u
SP=/tmp/claude-0/-home-user-ATTa/e4a0bfa4-ca7c-5b6e-8d79-41ccfb082a54/scratchpad
OUT="$SP/evidence/$1"; mkdir -p "$OUT"
H="${2:-atta-server}"; R="${3:-/srv/app-builder}"
docker exec -e R="$R" "$H" bash -c '
cd "$R" && tar czf /tmp/ev.tgz --ignore-failed-read \
  state/builds state/checklists state/runner/results state/runner/evidence.jsonl state/runner/learned_rules.json \
  state/runner/recipes state/maintenance state/alerts state/system-watcher.log state/library-results.json \
  state/coolify state/apps state/runner/evidence pipeline.log gateway.log run-state.json state/status.json state/app_numbers.json coolify_resources.json \
  library/UI_CAPABILITY_DEPLOYMENT_MANIFEST.json adm/state/journal adm/logs 2>/dev/null
ls library > /tmp/ev-library.txt; ls -la inbox > /tmp/ev-inbox.txt
journalctl -u app-builder-pipeline -u app-builder-watcher -u app-builder-gateway -u atta-deployd --no-pager --since "-6h" -o short-iso > /tmp/ev-journal.txt
deployctl status > /tmp/ev-deployctl.txt 2>&1; docker ps -a --format "{{.Names}}\t{{.Image}}\t{{.Status}}" > /tmp/ev-docker.txt
for s in app-builder-gateway app-builder-pipeline app-builder-watcher atta-deployd nginx docker; do echo "$s $(systemctl is-active $s)"; done > /tmp/ev-services.txt'
for f in ev.tgz ev-library.txt ev-inbox.txt ev-journal.txt ev-deployctl.txt ev-docker.txt ev-services.txt; do docker cp -q "$H:/tmp/$f" "$OUT/" 2>/dev/null || docker cp "$H:/tmp/$f" "$OUT/" >/dev/null; done
mkdir -p "$OUT/state" && tar xzf "$OUT/ev.tgz" -C "$OUT/state" && rm "$OUT/ev.tgz"
echo "snapshot -> $OUT ($(find "$OUT" -type f | wc -l) files)"
