'use strict';
// Runs the real ui-bridge/proxy.js + port.js + mover.mjs against a tiny fixture page
// and checks that the Playwright checks report what actually happens.
const test = require('node:test');
const assert = require('node:assert');
const path = require('path');
const { spawn } = require('child_process');
const { serveStatic, freePort } = require('../lib/serve');
const { runChecks } = require('../lib/check');
const { syncSkins, removeSkins } = require('../lib/skins');

test('checks against a fixture page through the real proxy', { timeout: 120000 }, async () => {
  const app = await serveStatic(path.join(__dirname, 'fixture'));
  const port = await freePort();
  const proxy = spawn(process.execPath, [path.join(__dirname, '..', 'ui-bridge', 'proxy.js'),
    '--app-id', 'fixture', '--target', app.url, '--port', String(port)]);
  try {
    await new Promise((resolve, reject) => {
      proxy.stdout.on('data', (d) => (/PASS/.test(d) ? resolve() : reject(new Error(String(d)))));
      proxy.on('exit', (c) => reject(new Error('proxy exited ' + c)));
    });
    const { checks } = await runChecks({ proxyUrl: `http://127.0.0.1:${port}`, appUrl: app.url, appId: 'fixture', skins: syncSkins('fixture') });
    const by = Object.fromEntries(checks.map((c) => [c.id, c]));
    for (const id of ['C1', 'C2', 'C3', 'C4', 'C5', 'C7', 'C8', 'C9', 'C10', 'C11', 'C12', 'C13', 'C14', 'C15', 'C16']) {
      assert.strictEqual(by[id].status, 'pass', `${id}: ${by[id].detail}`);
    }
    // Known mover limitation: translate does not move display:inline links on screen.
    assert.strictEqual(by.C6.status, 'fail');
    assert.match(by.C6.detail, /display:inline/);
  } finally {
    proxy.kill();
    app.stop();
    removeSkins('fixture');
  }
});
