# Running ATTa with Coolify

**ATTa** (the APP Builder) builds apps, puts each one through its six-stage watcher, and hands every **qualified** build to **Coolify**, which runs it.
This kit sets up and verifies the Coolify side of that hand-off.
ATTa's own code lives on the `claude/atta-hardening-v116` branch (release v116).
Its side of the contract is in `ATTa/docs/COOLIFY-HANDOFF.md` and `ATTa/04-deployment/coolify_handoff.py`.

```
 ┌──────────── ATTa server ─────────────┐          ┌─────────── Coolify server ───────────┐
 │ bash run  (nginx on 80/443, gateway, │  POST    │ scripts/install.sh  (this kit)       │
 │ pipeline, watcher, deployd)          │ /api/v1/ │ Coolify 4.3.23 + Traefik on 80/443   │
 │ coolify_handoff.py ──────────────────┼─deploy──►│ runs each qualified app              │
 │   COOLIFY_URL / COOLIFY_TOKEN (.env) │ port 8000│ scripts/connect-atta.sh → token      │
 └──────────────────────────────────────┘ private  └──────────────────────────────────────┘
```

Use **two servers**. Both ATTa's nginx and Coolify's proxy need ports 80/443.
ATTa's own installer notes say the same.

## Verified against a live Coolify

ATTa's docs say its hand-off *"has not yet been run against a live Coolify"*.
It has now: ATTa v116's unmodified `coolify_handoff.py` and `repair_actions.py` were run against Coolify 4.3.23 installed by this kit.

| ATTa behaviour | Result |
|---|---|
| `hand_off()` on a QUALIFIED build, `deploy`+`read` token | `DISPATCHED`. Coolify returned a `deployment_uuid` and the deployment **finished** |
| Dispatch again for the same build | Stays `DISPATCHED`, with no second deploy |
| App missing from `coolify_resources.json` | `BLOCKED_UNMAPPED` |
| Wrong UUID | `ERROR` / `HTTP 404 {"message":"No resources found."}`, then `RETRYING` |
| Self-heal `lookup_coolify_uuid("WhoAmI")` for an app named `whoami` | UUID found (case-insensitive) and written to `coolify_resources.json` |
| Lookup for a name with no app | Refused: "0 Coolify applications named 'ghost'" |
| Settings produced by `connect-atta.sh` | Parsed by ATTa's `envfile.py` with the token intact. Hand-off `DISPATCHED` through the server's private address (passes ATTa's `token_route_ok`) |

The kit's end-to-end test repeats ATTa's exact deploy call and acceptance rule on every run.

## Steps

### 1. Coolify server

Follow [RUNBOOK.md](RUNBOOK.md) steps 1-8.
To install from the checksum-pinned Coolify source zip that ATTa ships, rather than downloading the installer, set this in `config/coolify.env`:

```
COOLIFY_SOURCE_ZIP=/path/to/coolify-main.zip
```

The kit checks the zip's SHA-256 against the value ATTa v116 pins (`509f4abb…6609`) before running anything.
The zip's `scripts/install.sh` is byte-identical to the official v4.3.23 installer, so both paths install the same thing.

### 2. Connect ATTa (on the Coolify server)

```bash
sudo ./scripts/connect-atta.sh --map-apps
```

This does ATTa's manual Coolify-side steps for you:

1. Turns on API access.
2. Creates an API token with **only** `deploy` and `read`, and proves it can't write.
3. Writes `/root/atta-coolify/atta.env` with `COOLIFY_URL` and `COOLIFY_TOKEN`.
4. Writes `/root/atta-coolify/coolify_resources.json`, the app name → UUID map.

Re-running it rotates the token.

`COOLIFY_URL` defaults to this server's private address (`http://<private-ip>:8000`).
ATTa only sends its token over `https://`, or over `http://` to a private address.
The script refuses to write an `http://` URL on a public address, because ATTa would refuse to use it.
If your servers only share public IPs (Hetzner without a private network, for example), use the dashboard's HTTPS domain instead:

```bash
sudo ./scripts/connect-atta.sh --url https://coolify.yourdomain.com
```

### 3. ATTa server

```bash
cat atta.env >> /srv/app-builder/.env            # copy the file over first (it holds a secret)
systemctl restart app-builder-pipeline
```

Then set the cloud firewall: **port 8000 on the Coolify server open only to the ATTa server**.
(If you used the https URL, 8000 can be closed entirely.)

### 4. Tell Coolify how to run each app

For each app ATTa builds, add a resource in Coolify (**Projects → Add Resource**), **named exactly as ATTa names the app**, for example `grafana`.
ATTa's self-healer then finds the UUID by itself: it answers `BLOCKED_UNMAPPED` or a 404 with `lookup_coolify_uuid`.
Alternatively, copy `coolify_resources.json` from step 2 to `/srv/app-builder/coolify_resources.json`.
It's re-read on every retry, with no restart needed.

### 5. Watch the first hand-off

In ATTa, open `/builds/<id>` for a qualified build and check the `coolify` block:

| Status | Meaning | Fix |
|---|---|---|
| `DISPATCHED` | Coolify accepted every app | Done |
| `BLOCKED_NOT_CONFIGURED` | `COOLIFY_URL`/`COOLIFY_TOKEN` missing | Step 3, then restart the pipeline |
| `BLOCKED_UNMAPPED` | No UUID for an app | Step 4 |
| `RETRYING` + `REFUSED: … public address` | `http://` URL on a public IP | Step 2 with `--url https://…` |
| `RETRYING` + `HTTP 403 … API is disabled` | API access is off | Re-run `connect-atta.sh` |
| `RETRYING` + `HTTP 401/403` | Token revoked or rotated | Copy the new `atta.env` over (step 3) |
| `RETRYING` + `UNREACHABLE` | Firewall or wrong address | Check port 8000 from the ATTa server: `curl http://<coolify-private-ip>:8000/api/health` |
