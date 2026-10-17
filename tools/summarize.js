// Print de-duplicated errors from a crawl report.json
const r = JSON.parse(require('fs').readFileSync(process.argv[2], 'utf8'));
const uniq = (arr, f) => { const m = new Map(); for (const x of arr) { const k = f(x); m.set(k, (m.get(k) || 0) + 1); } return [...m].map(([k, n]) => (n > 1 ? `(x${n}) ` : '') + k); };
console.log('PAGES:', r.pages.length);
for (const p of r.pages) if (p.error || (p.status && p.status >= 400)) console.log('  PAGE-FAIL', p.url, p.status || '', p.error || '');
console.log('AUTH:'); for (const a of r.auth) console.log('  ', a.kind, a.url, a.status || '', a.result || '', a.error || '', '->', a.afterUrl || '', '|', (a.text || '').slice(0, 150));
console.log('CONSOLE ERRORS:'); uniq(r.consoleErrors, c => c.text.slice(0, 220) + (c.loc ? ' @' + c.loc : '')).slice(0, 40).forEach(x => console.log('  ', x));
console.log('PAGE ERRORS (uncaught JS):'); uniq(r.pageErrors, c => c.text.slice(0, 260)).slice(0, 30).forEach(x => console.log('  ', x));
console.log('HTTP >=400:'); uniq(r.httpErrors, c => `${c.status} ${c.method} ${c.url.replace(/^https?:\/\/[^/]+/, '')}`.slice(0, 200)).slice(0, 40).forEach(x => console.log('  ', x));
console.log('FAILED REQUESTS:'); uniq(r.failedRequests, c => `${c.err} ${c.method} ${c.url}`.slice(0, 200)).slice(0, 25).forEach(x => console.log('  ', x));
console.log('CLICKS:', r.clicks.length, 'errors:', r.clicks.filter(c => c.error).length); uniq(r.clicks.filter(c => c.error), c => c.error.slice(0, 120)).slice(0, 10).forEach(x => console.log('  ', x));
console.log('FORMS:'); for (const f of r.forms) console.log('  ', f.page, f.idx, f.submit || f.result || '', f.error || '', '->', f.after || '', '|', (f.text || '').slice(0, 100));
console.log('NOTES:', r.notes.join(' ; '));
