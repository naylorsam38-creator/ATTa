# ATTa

Front-end only: **proxy.js → port.js → mover.js**, plus a numbered pipeline that runs each app through them and records the results.

## Hook chain

```
APPLICATION HTML
  └─ <head>   proxy.js inserts, immediately before </head>:
              <script src="/_cp/port.js" data-capability-app="APP_ID" defer></script>
       └─ port.js       runs in the app's live page, builds the Port drawer
            └─ <section data-capability="button-mover" data-trust="local">
                         created by the Port, appended to ui.drawerBody()  (placement: "drawer")
                 └─ mover.js   mount(ctx) gets that section as ctx.slot,
                               then numbers / moves the app's own buttons and links in the live DOM
```

Nothing hooks into `#root` or any React/Vue/Angular component, or into a particular app button.

| File | Role |
|---|---|
| `cp/proxy.js` | Reverse proxy. Serves `/_cp/*` and injects the Port tag into HTML responses (`/<\/head\s*>/i`) |
| `cp/public/port.js` | Capability Port: drawer UI, capability registry, slot creation |
| `cp/public/capabilities/mover.js` | Button Mover: numbers buttons/links (`data-cp-n`), moves ↑/↓ within their parent, Reset restores |

Standalone proxy: `node cp/proxy.js http://localhost:3000 8080 my-app`

## Numbered pipeline

```
npm install                          # one dev dependency: playwright
node atta.js add <dir|git-url> [name]   # intake → apps/001-name, 002-name, …
node atta.js run                     # run every app (or: node atta.js run 002 005)
node atta.js results                 # rebuild results/RESULTS.md
```

For each app, the pipeline runs these stages:

1. **Build**: `npm ci` or `npm install`, then `npm run build` if the app has a build script
2. **Serve**: serves the first `index.html` it finds in `dist/`, `build/`, `out/`, `public/` or the app root. Otherwise it runs `npm start` with `PORT` set.
3. **Proxy**: puts `cp/proxy.js` in front of the app
4. **Playwright**: runs checks C1–C8 through the proxy
5. **Record**: writes `results/NNN-name/result.json`, `REPORT.md`, `screenshot.png` and `build.log`, and updates the summary in `results/RESULTS.md`

| Check | What it proves |
|---|---|
| C1 | Page loads through the proxy |
| C2 | Port tag sits immediately before `</head>` in the served HTML |
| C3 | `window.CapabilityPort` is live with the right app id |
| C4 | `section[data-capability="button-mover"]` has `data-trust`, is mounted, and sits inside `#_cp-drawer-body` |
| C5 | Mover finds and numbers the app's buttons and links. WARN if it finds none. |
| C6 | A move changes the live DOM order, and Reset restores it |
| C7 | The drawer opens from the CP toggle |
| C8 | No uncaught page errors |

Optional `atta.json` in an app sets overrides: `{ "build": "…", "start": "…", "static": "dist", "path": "/" }`.

`npm test` runs the proxy unit tests.
