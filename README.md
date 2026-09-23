# ATTa

This repo holds the Capability Port front end: **ui-bridge/proxy.js**, **capability-port/port.js** and **capability-port/caps/mover.mjs**. It also has a numbered pipeline that runs apps through them and records what happens.

The three system files are kept **verbatim**. They are byte-identical to the `out/` bundle shipped in every `*-capability-patched.zip`, and stage 0 of every run re-checks this. `capability-port/caps/sticky-notes.mjs` is new. It's a second capability written for this repo, and the proxy serves it the same way it serves the mover.

## Hook chain

```
APPLICATION HTML
  └─ <head>   ui-bridge/proxy.js inserts, immediately before </head>:
              <script src="/_cp/port.js" data-capability-hook="1" data-capability-app="APP_ID" defer></script>
       └─ capability-port/port.js      runs in the app's live page → window.__capabilityPort,
                                        UI in <div id="capability-port"> (open shadow root)
            └─ attach('/_cp/caps/mover.mjs')   (the proxy serves capability-port/caps/*.mjs at /_cp/caps/)
                 └─ <section data-capability="button-mover" data-trust="trusted">
                      created by the Port, appended to ui.drawerBody()   (mover: place = 'drawer')
                      └─ mover.mjs mount(ctx) gets it as ctx.slot, then works on the app's live DOM:
                         drag = CSS translate, double-click = rename, saved per screen in localStorage
```

### Skins, done the proxy's way

`proxy.js` has a built-in skin mechanism, and ATTa uses it as designed:

1. The proxy reads the customer from the `x-authenticated-customer-id` header.
2. It looks up `ui-bridge/layouts/<customer>/<app>.json`, which contains `{ "layout": "<skin>", "version": 1 }`.
3. It injects `<link rel="stylesheet" href="/_cs/<app>/<skin>.1.css">` before `</head>`.
4. It serves `ui-bridge/skins/<app>/<skin>.css` for that link.

With no header, the default customer has no layout, so the app keeps its original look.

The skins live in `skins-src/`:

| Customer | Skin | Look |
|---|---|---|
| acme | midnight | dark |
| globex | sunrise | warm, pill-shaped buttons |
| initech | blueprint | blue monochrome, monospace |

`lib/skins.js` writes the proxy's layout and skin files for every numbered app.

### Capabilities

| File | What it does |
|---|---|
| `capability-port/caps/mover.mjs` | Drag any button or link. Double-click to rename it. Moves and names are saved per screen. |
| `capability-port/caps/sticky-notes.mjs` | Pin coloured, draggable notes on the live app. Notes are saved per screen and come back on reload. |

Attach a capability by pasting its path into the Port's box, for example `/_cp/caps/sticky-notes.mjs`. The proxy serves every `capability-port/caps/*.mjs` file from the app's own origin, so capabilities load even on apps with a strict CSP.

`sticky-notes.mjs` sets all of its styles through `element.style`. That means it still renders on apps whose CSP refuses inline styles, such as Authelia.

Run the proxy on its own:

```
node ui-bridge/proxy.js --app-id my-app --target http://127.0.0.1:3000 --port 8080
```

## Numbered pipeline

```
npm install                               # one dev dependency: playwright
node atta.js add <zip|dir|git-url> [name]  # intake → apps/001-name, 002-name, …
node atta.js run                          # every app (or: node atta.js run 003 005)
node atta.js package [NNN ...]            # deploy bundle + docker compose verification (see below)
node atta.js results                      # rebuild results/RESULTS.md
npm test                                  # self-test: fixture page through the real proxy/port/mover
```

`add` numbers the app and unpacks it into `apps/NNN-name/src/`. **Numbers are permanent.** Re-sending the byte-identical zip keeps its existing number, and the extra copy is listed under `duplicates` in `intake.json`. A zip that differs at all gets the next number. For a zip it compares the bundled `out/` folder with the canonical files and records the result in `intake.json`. App source isn't committed.

Each app needs a recipe, `apps/NNN-name/atta.json`:

```json
{ "note": "…", "setup": ["cmd", "…"], "start": "cmd", "env": { "K": "V" }, "path": "/login", "timeout": 120 }
```

Setup commands run in `src/`. In the recipe values, `$SRC`, `$DATA` (the app's scratch folder) and `$PORT` are expanded.

For each app, the stages run in order:

| Stage | What happens |
|---|---|
| 0 intake | Checks the zip's `out/` bundle matches the canonical proxy.js, port.js and mover.mjs |
| 1 build | Runs the recipe's `setup` steps |
| 2 serve | Starts the app with `start`, gives it `PORT`, and waits until it answers |
| 3 proxy | Spawns `ui-bridge/proxy.js --app-id NNN-name --target <app>` and waits for `PASS` |
| 4 Playwright | Loads the app directly once as a baseline, then runs C1–C16 through the proxy |
| 5 video | Records the demo video (see below) |
| 6 record | Writes `results/NNN-name/`: `REPORT.md`, `result.json`, `build.log`, `screenshot-moved.png`, `screenshot-drawer.png`. Updates `results/RESULTS.md` |

| Check | What it proves |
|---|---|
| C1 | The page loads through the proxy, and the browser ends up still on the proxy |
| C2 | The exact hook tag appears once, immediately before `</head>` |
| C3 | `window.__capabilityPort` is live and reads the app id from the hook tag |
| C4 | `attach('/_cp/caps/mover.mjs')` creates `section[data-capability=button-mover]` in the drawer, with `data-trust` set |
| C5 | The page has buttons and links for the mover to work on |
| C6 | Dragging a **link** moves it on screen. The check measures the element's real position, not just its style. |
| C7 | Dragging a **button** moves it on screen |
| C8 | After a reload, the Port re-attaches the mover from storage and the mover re-applies the move |
| C9 | Double-clicking a control and entering a name through the prompt renames it |
| C10 | Reset puts every position and label back and clears what was saved |
| C11 | The Port's `+` button opens the drawer |
| C12 | No uncaught errors appear that the app doesn't also raise without the Port (checked against the baseline) |
| C13 | The app's Content-Security-Policy doesn't block the Port. Only violations the Port adds are counted. |
| C14 | Redirects, links, forms and assets stay on the proxy origin |
| C15 | Each test customer gets their skin through the proxy's `/_cs/` route, and no header gives the original look |
| C16 | The sticky-notes capability pins a visible note on the live page, keeps it after a reload, and clears it |

Stage **5 video** records `results/NNN-name/demo.mp4` (also `demo.webm`, which isn't committed). The demo runs through the Port's own UI:

1. Opens the app through the proxy.
2. Opens the Port and types `/_cp/caps/mover.mjs` to attach the mover.
3. Turns Move on, drags a button and a link, and renames one by double-clicking.
4. Attaches sticky notes, then pins a note and drags it into place.
5. Reloads as each customer (acme, globex, initech) to show their skin, with the moves and notes still there.
6. Returns to the original look, then presses Reset and Clear.

## Deploy bundles (AWS / Coolify)

```
node atta.js package [NNN ...]
```

Stage **6** builds `dist/NNN-name/` and `dist/NNN-name.zip`, which aren't committed.

- `app/` holds the app exactly as received, re-extracted from the original zip. The test pipeline's local `.env`, keys and build output are left out.
- `capability/` holds the proxy image: `proxy.js`, `port.js`, the caps, and this app's layouts and skins.
- `docker-compose.yml` is in Coolify's "Docker Compose" build-pack format. `proxy` is the only public web service, marked with `SERVICE_URL_PROXY_8080`, and it forwards to the app on the private network.
- `docker-compose.local.yml` publishes the proxy on `${PROXY_PORT:-8080}`. Use it for a plain Docker host or AWS EC2.

The per-app recipe lives in `apps/NNN-name/deploy/`. It holds the compose files, `deploy.json`, and `app-overlay/` for Dockerfiles and config added to `app/`.

After building, the stage runs `docker compose up --build` on the bundle and repeats the same Playwright checks against the containers. The results go in `results/NNN-name/DEPLOY.md` and `deploy.json`.

**Deploying on Coolify:**

1. Push the bundle folder to a git repo, or upload it.
2. Create a resource using the "Docker Compose" build pack.
3. Give the `proxy` service your domain.
4. Set the `SERVICE_PASSWORD_*` and other variables named in the compose file.

**Deploying on AWS (EC2 or Lightsail):**

```
docker compose -f docker-compose.yml -f docker-compose.local.yml up -d --build
```

Results: [results/RESULTS.md](results/RESULTS.md)
