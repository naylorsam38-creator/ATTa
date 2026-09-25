const http = require('http');
const secret = process.env.GUESTBOOK_SECRET;
if (!secret) { console.error('FATAL: GUESTBOOK_SECRET is required'); process.exit(1); }
http.createServer((req, res) => {
  res.writeHead(200, {'content-type': 'text/html; charset=utf-8'});
  res.end(`<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Env Guestbook</title></head><body><h1>Env Guestbook</h1><p>Signed with a ${secret.length}-character secret.</p></body></html>`);
}).listen(4000, '0.0.0.0', () => console.log('guestbook on 4000'));
