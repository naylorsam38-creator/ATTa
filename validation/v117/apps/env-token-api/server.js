const http = require('http');
if (!process.env.API_TOKEN) { console.error('Error: environment variable API_TOKEN must be set'); process.exit(1); }
http.createServer((req, res) => {
  res.writeHead(200, {'content-type': 'text/html; charset=utf-8'});
  res.end('<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Env Token API</title></head><body><h1>Env Token API</h1><p>Token configured.</p></body></html>');
}).listen(3000, '0.0.0.0', () => console.log('token api on 3000'));
