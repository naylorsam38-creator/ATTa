'use strict';
// Capability Port proxy.
//
// Sits in front of an application. Every request is forwarded to the app,
// except /_cp/* which is served from cp/public. When the app answers with
// HTML, the Port script tag is inserted immediately before </head>:
//
//   <script src="/_cp/port.js" data-capability-app="APP_ID" defer></script></head>
//
// That is the only hook into the application. Nothing else is rewritten.

const http = require('http');
const fs = require('fs');
const path = require('path');

const PUBLIC_DIR = path.join(__dirname, 'public');
const HEAD_CLOSE = /<\/head\s*>/i;

const MIME = {
  '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.svg': 'image/svg+xml',
};

function escapeAttr(s) {
  return String(s).replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;');
}

function portTag(appId) {
  return `<script src="/_cp/port.js" data-capability-app="${escapeAttr(appId)}" defer></script>`;
}

// Returns { html, injected }. Injects once, before the first </head>.
function inject(html, appId) {
  if (!HEAD_CLOSE.test(html)) return { html, injected: false };
  return { html: html.replace(HEAD_CLOSE, (m) => portTag(appId) + m), injected: true };
}

function serveCp(req, res) {
  const rel = decodeURIComponent(req.url.split('?')[0].slice('/_cp/'.length));
  const file = path.normalize(path.join(PUBLIC_DIR, rel));
  if (!file.startsWith(PUBLIC_DIR + path.sep)) {
    res.writeHead(403).end();
    return;
  }
  fs.readFile(file, (err, buf) => {
    if (err) {
      res.writeHead(404, { 'content-type': 'text/plain' }).end('not found');
      return;
    }
    res.writeHead(200, {
      'content-type': MIME[path.extname(file)] || 'application/octet-stream',
      'cache-control': 'no-store',
    });
    res.end(buf);
  });
}

function createProxy({ target, appId }) {
  const upstream = new URL(target);

  return http.createServer((req, res) => {
    if (req.url.startsWith('/_cp/')) return serveCp(req, res);

    const headers = { ...req.headers, host: upstream.host };
    // Ask for an uncompressed body so HTML can be rewritten.
    delete headers['accept-encoding'];

    const preq = http.request(
      {
        protocol: upstream.protocol,
        hostname: upstream.hostname,
        port: upstream.port,
        method: req.method,
        path: req.url,
        headers,
      },
      (pres) => {
        const type = String(pres.headers['content-type'] || '');
        if (!type.includes('text/html') || pres.headers['content-encoding']) {
          res.writeHead(pres.statusCode, pres.headers);
          pres.pipe(res);
          return;
        }
        const chunks = [];
        pres.on('data', (c) => chunks.push(c));
        pres.on('end', () => {
          const out = inject(Buffer.concat(chunks).toString('utf8'), appId);
          const h = { ...pres.headers };
          delete h['content-length'];
          h['x-cp-injected'] = out.injected ? '1' : '0';
          res.writeHead(pres.statusCode, h);
          res.end(out.html);
        });
      }
    );
    preq.on('error', (e) => {
      res.writeHead(502, { 'content-type': 'text/plain' }).end('upstream error: ' + e.message);
    });
    req.pipe(preq);
  });
}

module.exports = { createProxy, inject, portTag };

if (require.main === module) {
  const [target, port = '8080', appId = 'app'] = process.argv.slice(2);
  if (!target) {
    console.error('usage: node cp/proxy.js <target-url> [listen-port] [app-id]');
    process.exit(1);
  }
  createProxy({ target, appId }).listen(Number(port), () => {
    console.log(`Capability Port proxy :${port} -> ${target} (app ${appId})`);
  });
}
