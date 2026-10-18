# Handover — ATTa v117 + Coolify kit

Base: ATTa v114.2 (the newest code supplied; v115 was not in any upload). Release is now v117.

## Done in v117
- **Coolify runs ATTa.** `ATTa/Dockerfile` + `ATTa/docker-compose.coolify.yml`, entrypoint
  `04-deployment/container_main.py` (gateway, pipeline, watcher; Chromium inside for stage 6).
  `scripts/deploy-atta.sh` builds the image and creates/updates the Coolify service `atta`.
- **First start fetches the whole app list** (`<id>.library.json` inbox item, new in pipeline.py).
- **Each qualified app gets its own Coolify service** (`04-deployment/coolify_provision.py`, wired
  into `coolify_handoff.py`): built from the recipe that qualified, with the same fence, and with
  its skin/capability adapter proxy (`atta-skin`) in front, frozen per build. Re-qualified apps
  update their own service; hand-made Coolify resources are never touched.
- Coolify "Service ... started" deploy answers now count as accepted (they have no deployment_uuid).
- Token from `connect-atta.sh` is now read+write+deploy (needed to create services).
- Removed: the duplicate 38-entry `config/app-list.json`, the broken `deploy-app-list.sh`, and the
  reskinned `coolify-main.zip` that failed the pinned checksum (the pinned original is `05-coolify/coolify-main.zip`).
- Restored executable permissions on every kit script (the uploaded kit had lost them).

## Tests
- ATTa: `cd ATTa && python3 -m unittest discover -s tests` -> 76 OK, 1 skipped (31 new in test_v117_coolify.py,
  against a fake Coolify that answers like the 4.3.23 source).
- Kit: `bats tests/unit` -> 67 OK.

## Not proven yet (needs a real server)
- `docker build` of the ATTa image, and a full run under a live Coolify.
- Still open from v114: ATTa's own `05-coolify/install-coolify.sh` fixes (admin email/password check,
  `env USER=root HOME=/root`, proxy stability) were never done; `deploy-atta.sh` does not use that script.
