'use strict';
// Writes the proxy's own skin inputs for an app, in the format ui-bridge/proxy.js reads:
//   ui-bridge/layouts/<customer>/<appId>.json   { "layout": "<skin>", "version": 1 }
//   ui-bridge/skins/<appId>/<skin>.css
// from skins-src/<skin>.css and skins-src/customers.json ({ customer: skin }).
const fs = require('fs');
const path = require('path');

const ROOT = path.join(__dirname, '..');
const SRC = path.join(ROOT, 'skins-src');
const BRIDGE = path.join(ROOT, 'ui-bridge');

function customers() {
  try { return JSON.parse(fs.readFileSync(path.join(SRC, 'customers.json'), 'utf8')); } catch (_) { return {}; }
}

function syncSkins(appId) {
  const map = {};
  for (const [customer, skin] of Object.entries(customers())) {
    if (customer.startsWith('_')) continue;
    const css = path.join(SRC, `${skin}.css`);
    if (!fs.existsSync(css)) continue;
    fs.mkdirSync(path.join(BRIDGE, 'layouts', customer), { recursive: true });
    fs.writeFileSync(path.join(BRIDGE, 'layouts', customer, `${appId}.json`), JSON.stringify({ layout: skin, version: 1 }, null, 2) + '\n');
    fs.mkdirSync(path.join(BRIDGE, 'skins', appId), { recursive: true });
    fs.copyFileSync(css, path.join(BRIDGE, 'skins', appId, `${skin}.css`));
    map[customer] = skin;
  }
  return map;
}

function removeSkins(appId) {
  for (const customer of Object.keys(customers())) {
    fs.rmSync(path.join(BRIDGE, 'layouts', customer, `${appId}.json`), { force: true });
  }
  fs.rmSync(path.join(BRIDGE, 'skins', appId), { recursive: true, force: true });
}

module.exports = { syncSkins, removeSkins };
