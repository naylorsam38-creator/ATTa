#!/usr/bin/env bash
# handoff_watch.sh MAX_S — log every change in ATTa's per-build Coolify block (laptop) and Coolify's deployments.
MAX=${1:-2400}; t0=$(date +%s); last=""
while [ $(( $(date +%s)-t0 )) -lt $MAX ]; do
  a=$(docker exec -u sam atta-laptop python3 -c "
import json,glob,os
for f in sorted(glob.glob(os.path.expanduser('~/.app-builder-local/state/builds/*.json'))):
    d=json.load(open(f)); c=d.get('coolify')
    if not c: continue
    h=(d.get('healing') or {}).get('handoff') or {}
    apps=' '.join(f\"{k}:{v.get('status')}\" for k,v in sorted((c.get('apps') or {}).items()))
    print(d['id'][-8:], c.get('status'), 'att='+str(c.get('attempts')), apps, '| heal='+str(h.get('status'))+'/'+str(h.get('rounds')), '|', (c.get('last_error') or '')[:90])
")
  b=$(docker exec coolify-server bash -c 'docker exec coolify php artisan tinker --execute="foreach(App\Models\ApplicationDeploymentQueue::orderBy(\"id\")->get() as \$d){echo \$d->application_name.\"=\".\$d->status.\" \";}"' 2>/dev/null | tail -1)
  cur="$a
COOLIFY: $b"
  if [ "$cur" != "$last" ]; then echo "===== $(date -u +%T)"; echo "$cur"; last="$cur"; fi
  sleep 20
done
