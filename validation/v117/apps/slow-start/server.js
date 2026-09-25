const http = require('http'); const page = t => `<!doctype html><html lang="en"><head><meta charset="utf-8"><title>${t}</title></head><body><h1>${t}</h1></body></html>`;
console.log('warming caches...');
setTimeout(() => http.createServer((q, r) => { r.writeHead(200, {'content-type': 'text/html'}); r.end(page('Slow Start')); }).listen(3000, '0.0.0.0', () => console.log('listening on 3000')), 75000);
