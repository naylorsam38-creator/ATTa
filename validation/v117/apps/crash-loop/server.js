const http = require('http'); const page = t => `<!doctype html><html lang="en"><head><meta charset="utf-8"><title>${t}</title></head><body><h1>${t}</h1></body></html>`;
http.createServer((q, r) => { r.writeHead(200, {'content-type': 'text/html'}); r.end(page('Crash Loop')); }).listen(3000, '0.0.0.0', () => console.log('listening on 3000'));
setTimeout(() => { console.error('FATAL: worker pool exhausted, exiting'); process.exit(1); }, 20000);
