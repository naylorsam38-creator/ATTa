# ATTa

This repo holds the Capability Port front end: **ui-bridge/proxy.js**, **capability-port/port.js** and **capability-port/caps/mover.mjs**. It also has a numbered pipeline that runs apps through them and records what happens.

The three system files are kept **verbatim**. They are byte-identical to the `out/` bundle shipped in every `*-capability-patched.zip`, and stage 0 of every run re-checks this.

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

Run the proxy on its own:

```
node ui-bridge/proxy.js --app-id my-app --target http://127.0.0.1:3000 --port 8080
```

## Numbered pipeline

```
npm install                               # one dev dependency: playwright
node atta.js add <zip|dir|git-url> [name]  # intake → apps/001-name, 002-name, …
node atta.js run                          # every app (or: node atta.js run 003 005)
node atta.js results                      # rebuild results/RESULTS.md
npm test                                  # self-test: fixture page through the real proxy/port/mover
```

`add` numbers the app and unpacks it into `apps/NNN-name/src/`. For a zip it compares the bundled `out/` folder with the canonical files and records the result in `intake.json`. App source isn't committed.

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
| 4 Playwright | Loads the app directly once as a baseline, then runs C1–C14 through the proxy |
| 5 record | Writes `results/NNN-name/`: `REPORT.md`, `result.json`, `build.log`, `screenshot-moved.png`, `screenshot-drawer.png`. Updates `results/RESULTS.md` |

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

Results: [results/RESULTS.md](results/RESULTS.md)
