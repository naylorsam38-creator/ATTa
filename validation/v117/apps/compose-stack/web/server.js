// Visit counter backed by Redis, speaking RESP directly (no npm dependencies).
const http = require('http'), net = require('net');
const REDIS_HOST = process.env.REDIS_HOST || 'redis';
function incr(key) {
  return new Promise((resolve, reject) => {
    const s = net.createConnection(6379, REDIS_HOST);
    s.setTimeout(3000, () => { s.destroy(); reject(new Error('redis timeout')); });
    s.on('error', reject);
    s.on('data', d => { s.end(); const m = /^:(\d+)/.exec(d.toString()); m ? resolve(Number(m[1])) : reject(new Error(d.toString())); });
    s.write(`*2\r\n$4\r\nINCR\r\n$${key.length}\r\n${key}\r\n`);
  });
}
http.createServer(async (req, res) => {
  try {
    const n = await incr('visits');
    res.writeHead(200, {'content-type': 'text/html; charset=utf-8'});
    res.end(`<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Compose Stack</title></head><body><h1>Compose Stack</h1><p>Visits: ${n}</p></body></html>`);
  } catch (e) {
    res.writeHead(503, {'content-type': 'text/plain'}); res.end('redis unavailable: ' + e.message);
  }
}).listen(8080, '0.0.0.0', () => console.log('compose-stack web on 8080'));
