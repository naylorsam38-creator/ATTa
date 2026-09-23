'use strict';
// Getting an app running: static directory or its own start script.
const http = require('http');
const fs = require('fs');
const path = require('path');
const net = require('net');
const { spawn } = require('child_process');

const TYPES = {
  '.html': 'text/html; charset=utf-8', '.js': 'text/javascript', '.mjs': 'text/javascript',
  '.css': 'text/css', '.json': 'application/json', '.svg': 'image/svg+xml', '.png': 'image/png',
  '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.gif': 'image/gif', '.webp': 'image/webp',
  '.ico': 'image/x-icon', '.woff': 'font/woff', '.woff2': 'font/woff2', '.txt': 'text/plain',
};

function freePort() {
  return new Promise((resolve, reject) => {
    const s = net.createServer();
    s.unref();
    s.on('error', reject);
    s.listen(0, () => {
      const { port } = s.address();
      s.close(() => resolve(port));
    });
  });
}

function listen(server) {
  return freePort().then((port) => new Promise((resolve) => server.listen(port, () => resolve(port))));
}

// Static server with SPA fallback to index.html.
async function serveStatic(dir) {
  const root = path.resolve(dir);
  const server = http.createServer((req, res) => {
    let rel = decodeURIComponent(req.url.split('?')[0]);
    let file = path.normalize(path.join(root, rel));
    if (!file.startsWith(root)) return res.writeHead(403).end();
    if (fs.existsSync(file) && fs.statSync(file).isDirectory()) file = path.join(file, 'index.html');
    if (!fs.existsSync(file)) {
      if (path.extname(rel)) return res.writeHead(404).end();
      file = path.join(root, 'index.html');
    }
    res.writeHead(200, { 'content-type': TYPES[path.extname(file)] || 'application/octet-stream' });
    fs.createReadStream(file).pipe(res);
  });
  const port = await listen(server);
  return { url: `http://127.0.0.1:${port}`, stop: () => server.close() };
}

async function waitForHttp(url, timeoutMs, proc) {
  const end = Date.now() + timeoutMs;
  while (Date.now() < end) {
    if (proc && proc.exitCode !== null) throw new Error(`start command exited with ${proc.exitCode}`);
    const ok = await new Promise((resolve) => {
      http.get(url, (r) => { r.resume(); resolve(true); }).on('error', () => resolve(false));
    });
    if (ok) return;
    await new Promise((r) => setTimeout(r, 500));
  }
  throw new Error(`app did not answer on ${url} within ${timeoutMs / 1000}s`);
}

// Runs the app's own server (already told its port), waits for it to answer.
async function serveCommand(cmd, cwd, log, timeoutMs, port, env) {
  const proc = spawn(cmd, { cwd, shell: '/bin/bash', detached: true, env: { BROWSER: 'none', ...env } });
  proc.stdout.on('data', (d) => log(d.toString()));
  proc.stderr.on('data', (d) => log(d.toString()));
  const url = `http://127.0.0.1:${port}`;
  const stop = () => { try { process.kill(-proc.pid, 'SIGTERM'); } catch (_) {} };
  try {
    await waitForHttp(url, timeoutMs, proc);
  } catch (e) {
    stop();
    throw e;
  }
  return { url, stop };
}

module.exports = { serveStatic, serveCommand, freePort, listen };
