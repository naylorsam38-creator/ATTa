const http = require('http');
const port = Number(process.env.PORT || 3000);
let hits = 0;
http.createServer((req, res) => {
  hits++;
  if (req.url === '/api/health') { res.writeHead(200, {'content-type': 'application/json'}); return res.end(JSON.stringify({ok: true})); }
  if (req.url === '/api/items') { res.writeHead(200, {'content-type': 'application/json'}); return res.end(JSON.stringify([{id: 1, name: 'alpha'}, {id: 2, name: 'beta'}])); }
  res.writeHead(200, {'content-type': 'text/html; charset=utf-8'});
  res.end(`<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Node API</title></head><body><h1>Node API</h1><p>Requests served: ${hits}</p></body></html>`);
}).listen(port, '0.0.0.0', () => console.log(`node-api listening on ${port}`));
