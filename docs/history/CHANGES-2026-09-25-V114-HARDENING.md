# v114 — security hardening (2026-09-25)

Built on v112. Nothing in the worker pool, the six-stage gate, ADM's deploy steps, the rule lifecycle or
the heal chain was replaced. What changed, and what proved it.

## The holes
1. **Any logged-in account could take over the server.** A zip containing
   `UI_Skin_Capability_OneShot_v2.zip` is treated as an ATTa bundle. For any uploader the pipeline then
   (a) replaced the skins package and immediately ran its `out/install_all.py`, (b) replaced the Front
   Door page every user sees, and (c) queued a root deploy with ADM, which runs the bundle's `bash run`.
   A tester account was enough for all three.
2. **Uploaded apps ran with few limits.** Plain `docker run` with only a memory cap; compose files could
   ask for `privileged`, `network_mode: host`, extra capabilities, or bind-mount the Docker socket or `/`.
3. **Containers could reach the cloud metadata service** (169.254.169.254), which on AWS gives out the
   server's IAM credentials.
4. **ADM staging had no size limit**, so a zip bomb could fill the disk.
5. **The LLM repair step sent logs and build records off the server unredacted.**

## What was built
1. `pipeline.py`: `may_update_system(owner)`. The bundle branch refuses non-admins **before** anything
   is touched (package, Front Door, ADM). The build fails with a plain reason and is marked `refused`,
   so `maintenance.on_failure` doesn't send it to self-healing. `queue_system_update` checks again.
   `system` (a zip placed in the inbox on the server) stays trusted: it needs server access already.
2. `deployd/adm/authz.py` (new): ADM checks the account itself. A web-queued job runs only if its
   `requested_by` is an enabled admin in the live accounts file; `deployctl`, `incoming/` and `system`
   are local and trusted. An unreadable accounts file refuses web jobs.
3. `app_runner.py`, every container: `no-new-privileges`, `--pids-limit` (1024), `--cpus` (2.0),
   `--cap-drop ALL` plus a small safe set (CHOWN, DAC_OVERRIDE, FOWNER, FSETID, KILL, SETGID, SETUID,
   SETPCAP, NET_BIND_SERVICE). Compose services get the same, and lose `privileged`, `devices`,
   `host` network/pid/ipc/uts modes, unconfined security options, extra capabilities, and bind mounts
   outside the app's folder (read-only time-zone files excepted). Ports stay on 127.0.0.1.
   New rule `container.caps` (checked after all specific rules): "Operation not permitted" retries once
   with Docker's default capabilities, other limits kept. The saved recipe remembers it.
   `-v` values that aren't plain container paths are ignored.
4. `bootstrap.sh`: `atta-block-metadata.service` drops container traffic to 169.254.169.254 (and the
   IPv6 equivalent) in Docker's `DOCKER-USER` chain, re-applied every time Docker starts. On AWS, if
   the AWS CLI and permission exist, it also requires IMDSv2 with hop limit 1.
5. `deployd/adm/staging.py`: total unpacked size (2 GB), file count (20,000) and per-file compression
   ratio (200:1 above 1 MB) are capped; bytes are counted as written, so a lying header can't pass.
6. `redact.py` (new) + `llm_repair.py`: everything sent to the model is redacted (NAME=value secrets,
   URL passwords, Authorization headers, PEM keys, AWS/Anthropic/GitHub key shapes). Names stay visible.
   A write the model asks for that contains the redaction marker is refused, so a hidden value is never
   written back over the real one.

## Settings (all in the service environment, `.env`)
| Variable | Default | What |
|---|---|---|
| `APP_BUILDER_HARDEN` | `true` | `false` turns all container limits off |
| `APP_BUILDER_PIDS_LIMIT` | `1024` | processes per container |
| `APP_BUILDER_CPUS` | `2.0` | CPUs per container (`0` = no cap) |
| `APP_BUILDER_ALLOW_HOST_ACCESS` | `false` | `true` lets compose files keep host mounts/modes (Docker managers like Portainer and Coolify need the socket) |
| `ATTA_ADM_MAX_BUNDLE_BYTES` / `_FILES` / `_COMPRESSION_RATIO` | 2 GB / 20000 / 200 | ADM staging limits |

## What proved it
`tests/test_v114_hardening.py`, 20 tests, no Docker or root needed (`python3 -m unittest discover -s tests`):
a tester's bundle leaves the package, Front Door and ADM untouched and isn't sent to healing; ADM refuses a
non-admin job without backing up or activating; zip traversal, bomb, size and count limits; hardened
`docker run` flags and compose service rewrite; rule ordering; recipe memory; redaction; and the LLM loop
with a fake model (nothing secret sent, marker write refused).

**Not yet proved on a real server:** the container limits and the metadata rule need a run on the AWS box
with real apps. Watch for new `container.caps` retries in the build pages; an app that still fails after
the retry is the one to look at.

## Not in v114
- Gateway and pipeline still run as root. They drive Docker, and Docker access is root-equivalent, so a
  real split needs rootless Docker or a small privileged helper. That's its own change.
- The loose files at the top of the zip (`04-deployment/*.py`, `bootstrap.sh`, `aws-cloud-init.yaml`,
  `deployd/atta.service`, `scripts/`) are outside `ATTa/`, unused and unchanged.
