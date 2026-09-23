'use strict';
const test = require('node:test');
const assert = require('node:assert');
const http = require('http');
const { createProxy, inject } = require('../cp/proxy');
const { listen } = require('../lib/serve');

const TAG = '<script src="/_cp/port.js" data-capability-app="APP" defer></script>';

test('inject puts the Port tag immediately before </head>', () => {
  const r = inject('<html><head><title>x</title></head><body></body></html>', 'APP');
  assert.ok(r.injected);
  assert.ok(r.html.includes(TAG + '</head>'));
});

test('inject matches </head> case-insensitively and with whitespace', () => {
  const r = inject('<HEAD></HEAD  ><body>', 'APP');
  assert.ok(r.html.includes(TAG + '</HEAD  >'));
});

test('inject escapes the app id and leaves HTML without </head> alone', () => {
  assert.ok(inject('<head></head>', 'a"b').html.includes('data-capability-app="a&quot;b"'));
  const r = inject('<body>no head</body>', 'APP');
  assert.strictEqual(r.injected, false);
  assert.strictEqual(r.html, '<body>no head</body>');
});

function get(url) {
  return new Promise((resolve, reject) => {
    http.get(url, (res) => {
      let body = '';
      res.on('data', (c) => (body += c));
      res.on('end', () => resolve({ res, body }));
    }).on('error', reject);
  });
}

test('proxy rewrites HTML, passes other types through, serves /_cp/', async () => {
  const app = http.createServer((req, res) => {
    if (req.url === '/data.json') return res.writeHead(200, { 'content-type': 'application/json' }).end('{"a":"</head>"}');
    res.writeHead(200, { 'content-type': 'text/html', 'content-length': 26 }).end('<head></head><body></body>');
  });
  const appPort = await listen(app);
  const proxy = createProxy({ target: `http://127.0.0.1:${appPort}`, appId: 'APP' });
  const port = await listen(proxy);
  try {
    const html = await get(`http://127.0.0.1:${port}/`);
    assert.strictEqual(html.res.headers['x-cp-injected'], '1');
    assert.ok(html.body.includes(TAG + '</head>'));
    const json = await get(`http://127.0.0.1:${port}/data.json`);
    assert.strictEqual(json.body, '{"a":"</head>"}');
    const js = await get(`http://127.0.0.1:${port}/_cp/port.js`);
    assert.strictEqual(js.res.statusCode, 200);
    assert.ok(js.body.includes('CapabilityPort'));
    const mover = await get(`http://127.0.0.1:${port}/_cp/capabilities/mover.js`);
    assert.ok(mover.body.includes("placement: 'drawer'"));
    const escape = await get(`http://127.0.0.1:${port}/_cp/..%2f..%2fpackage.json`);
    assert.strictEqual(escape.res.statusCode, 403);
  } finally {
    proxy.close();
    proxy.closeAllConnections();
    app.close();
    app.closeAllConnections();
  }
});
