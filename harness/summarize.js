// Print a compact digest of playwright.json for report writing.
const R = require(require('path').resolve(process.argv[2]));
const cap = (a, n = 15) => a.slice(0, n);
console.log('PAGES:'); R.pages.forEach(p => console.log(`  ${p.status ?? 'ERR'} ${p.url}${p.finalUrl !== p.url ? ' -> ' + p.finalUrl : ''}${p.error ? ' :: ' + p.error : ''}`));
console.log(`CONSOLE ERRORS (${R.consoleErrors.length}):`); cap(R.consoleErrors).forEach(e => console.log(`  [${e.url}] ${e.text}`));
console.log(`PAGE ERRORS (${R.pageErrors.length}):`); cap(R.pageErrors).forEach(e => console.log(`  [${e.url}] ${e.text}`));
console.log(`FAILED REQUESTS (${R.failedRequests.length}):`); cap(R.failedRequests).forEach(e => console.log(`  ${e.method} ${e.url} ${e.error}`));
console.log(`HTTP>=400 (${R.badResponses.length}):`); cap(R.badResponses, 25).forEach(e => console.log(`  ${e.status} ${e.method} ${e.url}`));
console.log(`FORMS (${R.forms.length}):`); R.forms.forEach(f => console.log(`  ${f.page} [${f.submitLabel}] filled=${f.filled} -> ${f.afterUrl} | ${f.validation.join(' / ').slice(0, 300)}`));
console.log(`CLICKS: ${R.clicks.length}; SKIPPED (safety): ${R.skipped.length}`); cap(R.skipped, 8).forEach(s => console.log(`  ${s.reason}: ${s.text || s.label || s.href}`));
if (R.notes.length) console.log('NOTES:', cap(R.notes, 5));
