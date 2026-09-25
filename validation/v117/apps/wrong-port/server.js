const http = require('http'); const page = t => `<!doctype html><html lang="en"><head><meta charset="utf-8"><title>${t}</title></head><body><h1>${t}</h1></body></html>`;
http.createServer((q, r) => { r.writeHead(200, {'content-type': 'text/html'}); r.end(page('Wrong Port')); }).listen(5055, '0.0.0.0', () => console.log('listening on 5055'));
