// Shared browser launcher: local test hostnames bypass the sandbox egress proxy.
const { chromium } = require('/opt/node-tools/node_modules/playwright');
const BYPASS = '<-loopback>,localhost,*.localhost,127.0.0.1,*.example.com,example.com,*.test,*.local';
module.exports.launch = (extra = {}) => chromium.launch(Object.assign({
  proxy: process.env.HTTPS_PROXY ? { server: process.env.HTTPS_PROXY, bypass: BYPASS } : undefined,
}, extra));
module.exports.chromium = chromium;
