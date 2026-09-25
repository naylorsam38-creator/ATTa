# Secrets: ATTa's own vs. the customer's (v115)

Two kinds of secret meet when ATTa starts an app. They are kept apart by design.

| | ATTa secrets | Customer secrets |
|---|---|---|
| Examples | session secret, ATTa's Anthropic key, `COOLIFY_TOKEN`, Docker Hub login, `ALERT_WEBHOOK_URL`, cloud credentials | Stripe, PayPal, OpenAI, Anthropic, email-provider tokens chosen for one app |
| Where they live | `/srv/app-builder/.env` (root, 0600) and the services' environment | the app's own `.env` (today); the client form (planned) |
| Reach an app? | **Never** | **Yes**, the app they were supplied for |

## How the separation works

1. **Clean environment.** Every `docker` and `docker compose` command runs with only Docker's own settings
   (`PATH`, `HOME`, `DOCKER_*`, proxy variables) plus the app's approved values. A compose file that asks for
   `${APP_BUILDER_SESSION_SECRET}` or `${ANTHROPIC_API_KEY}` gets **its own** value: the one in the app's
   `.env`, or a freshly generated one. Before v115 it got ATTa's, and ATTa's key even **replaced** a key the
   customer had put in the app's `.env`.
2. **Paths stay inside the app.** The compose YAML is read first (following `include:` and `extends: file:`),
   then compose renders it once without reading env files, so every file it would read is visible, and once
   for real. Throughout, these must be inside the app's folder (or its own runner work folder), with symlinks
   resolved: `env_file`, files pulled in by `include:`/`extends:`, `secrets`/`configs` `file:`,
   `build.context`, `build.dockerfile`, `additional_contexts`, and the project `.env`. On a Docker Compose too
   old for `config --no-env-resolution` the YAML check alone covers env files; if PyYAML is also missing the
   app's compose file is refused (install `python3-yaml` or a newer Compose) rather than run unchecked. Bind-style
   named volumes (`driver_opts: {type: none, o: bind, device: ...}`) are refused. `build.ssh` and secrets
   sourced from the runner's `environment:` are refused. Service bind mounts outside the folder are dropped
   (not refused) as since v114, so the app can still start.
3. **Value scan.** The final configuration (and the `-e` list of a `docker run` start) must not contain the
   value of any ATTa secret, under any name, raw, base64 or URL-encoded. Protected: every secret-looking entry
   of ATTa's `.env` and of the service environment, plus Docker's stored logins. Values shorter than 8
   characters are ignored. A customer key with the same name as one of ATTa's passes; a customer value that
   is identical to ATTa's is refused.

A refusal starts with `ATTA SECURITY REFUSED:`, names the setting and the kind of secret (never a value),
and is final for that way of starting the app: self-healing does not try to "fix" it. The app's other ways
to start (its image, its Dockerfile) are still tried.

## Customer tokens today

Put them in the app's own `.env` (next to its compose file), as the app's documentation says. Compose reads
that file for that app only. **Do not** put a customer's token in `/srv/app-builder/.env`: that file is ATTa's,
everything secret-looking in it is protected, and an app that receives it is refused.

The client form (collect a customer's service tokens per app, store them encrypted, inject them only into
that app) is the next step; it plugs into the same place (`part["env"]`) and the same scan.

## Apps that need the host (Portainer, Coolify, other Docker managers)

The old server-wide `APP_BUILDER_ALLOW_HOST_ACCESS=true` handed the Docker socket (root on this server) to
**every** uploaded app. It is now ignored (the build notes say so). Trust one app at a time instead:

```
sudo python3 /opt/app-builder/trusted_apps.py trust portainer --reason "needs the Docker socket"
sudo python3 /opt/app-builder/trusted_apps.py list
sudo python3 /opt/app-builder/trusted_apps.py untrust portainer
```

The list (`/srv/app-builder/state/trusted_apps.json`) is only honoured while it is a root-owned file nobody
else can write. Each use is written into that app's build notes with who approved it, when and why. A trusted
app keeps host mounts and host modes; it still never receives ATTa's secrets, and its `env_file`, `secrets`,
`configs` and build contexts must still stay inside its folder.
