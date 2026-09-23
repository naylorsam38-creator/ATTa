'use strict';
// Writes the proxy's own skin inputs for an app, in the format ui-bridge/proxy.js reads:
//   ui-bridge/layouts/<customer>/<appId>.json   { "layout": "<skin>", "version": 1 }
//   ui-bridge/skins/<appId>/<skin>.css
// from the skin library: skins-library/<category>/<skin>.css (category per app: atta.json "skinCategory")
// and skins-library/customers.json ({ customer: skin }).
const fs = require('fs');
const path = require('path');

const ROOT = path.join(__dirname, '..');
const LIB = path.join(ROOT, 'skins-library');
const BRIDGE = path.join(ROOT, 'ui-bridge');

const readJson = (f) => { try { return JSON.parse(fs.readFileSync(f, 'utf8')); } catch (_) { return null; } };
const customers = () => Object.fromEntries(Object.entries(readJson(path.join(LIB, 'customers.json')) || {}).filter(([k]) => !k.startsWith('_')));

function categoryFor(appId) {
  const cfg = readJson(path.join(ROOT, 'apps', appId, 'atta.json')) || {};
  return cfg.skinCategory && fs.existsSync(path.join(LIB, cfg.skinCategory)) ? cfg.skinCategory : 'dashboard';
}

// → { customer: { skin, label, category } }
function syncSkins(appId) {
  const category = categoryFor(appId);
  fs.rmSync(path.join(BRIDGE, 'skins', appId), { recursive: true, force: true });
  const map = {};
  for (const [customer, skin] of Object.entries(customers())) {
    const css = path.join(LIB, category, `${skin}.css`);
    if (!fs.existsSync(css)) continue;
    fs.mkdirSync(path.join(BRIDGE, 'layouts', customer), { recursive: true });
    fs.writeFileSync(path.join(BRIDGE, 'layouts', customer, `${appId}.json`), JSON.stringify({ layout: skin, version: 1 }, null, 2) + '\n');
    fs.mkdirSync(path.join(BRIDGE, 'skins', appId), { recursive: true });
    fs.copyFileSync(css, path.join(BRIDGE, 'skins', appId, `${skin}.css`));
    const meta = readJson(path.join(LIB, category, `${skin}.json`)) || {};
    map[customer] = { skin, label: meta.label || skin, category };
  }
  return map;
}

function removeSkins(appId) {
  if (fs.existsSync(path.join(BRIDGE, 'layouts'))) {
    for (const customer of fs.readdirSync(path.join(BRIDGE, 'layouts'))) {
      fs.rmSync(path.join(BRIDGE, 'layouts', customer, `${appId}.json`), { force: true });
    }
  }
  fs.rmSync(path.join(BRIDGE, 'skins', appId), { recursive: true, force: true });
}

module.exports = { syncSkins, removeSkins, categoryFor };
