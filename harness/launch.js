// Shared Chromium launcher: session proxy for external assets, direct for local app hosts.
// Chromium flags are used instead of Playwright's `proxy` option, which forces loopback through the proxy.
const { chromium } = require('playwright');
const BYPASS = 'localhost;127.0.0.1;*.localhost;*.example.com;example.com;*.test;' + (process.env.PW_BYPASS || '');
module.exports = () => chromium.launch({
  args: ['--no-sandbox', ...(process.env.HTTPS_PROXY ? [`--proxy-server=${process.env.HTTPS_PROXY}`, `--proxy-bypass-list=${BYPASS}`] : [])],
});
