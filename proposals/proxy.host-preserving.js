#!/usr/bin/env node
'use strict';

const fs = require('fs');
const path = require('path');
const http = require('http');
const https = require('https');
const zlib = require('zlib');

const APPS_DIR = path.join(__dirname, 'apps');
const LAYOUTS_DIR = path.join(__dirname, 'layouts');
const SKINS_DIR = path.join(__dirname, 'skins');

const DEFAULT_HOST = '127.0.0.1';
const DEFAULT_PORT = 8080;
const DEFAULT_CUSTOMER_HEADER = 'x-authenticated-customer-id';
// Real traffic does not carry a customer header by default.
// Resolve to the deterministic default skin instead of withholding the stylesheet.
const DEFAULT_CUSTOMER_ID = process.env.APP_BUILDER_CUSTOMER_ID || 'default';
const ID_PATTERN = /^[a-z0-9-]+$/;
const HTML_PATTERN = /^\s*text\/html(?:\s*;|$)/i;

const PORT_ROUTE = '/_cp/port.js';
const PORT_FILE = path.join(__dirname, '..', 'capability-port', 'port.js');
const HOOK_MARKER = 'data-capability-hook';
const HOOK_ALWAYS = true;
// Same-origin route for capability modules in capability-port/caps (lets pages with a CSP load them).
const CAPS_ROUTE = /^\/_cp\/caps\/([a-z0-9-]+\.mjs)$/;
const CAPS_DIR = path.join(__dirname, '..', 'capability-port', 'caps');

const HOP_BY_HOP_HEADERS = new Set([
  'connection',
  'keep-alive',
  'proxy-authenticate',
  'proxy-authorization',
  'te',
  'trailer',
  'transfer-encoding',
  'upgrade'
]);

function parseArguments(args) {
  const values = {
    host: DEFAULT_HOST,
    port: DEFAULT_PORT,
    customerHeader: DEFAULT_CUSTOMER_HEADER
  };

  const allowed = new Set([
    '--app-id',
    '--target',
    '--host',
    '--port',
    '--customer-header',
    '--customer-id'
  ]);

  for (let index = 0; index < args.length; index += 1) {
    const name = args[index];
    const value = args[index + 1];

    if (!allowed.has(name)) throw new Error(`Unknown argument: ${name}`);
    if (!value || value.startsWith('--')) throw new Error(`Missing value for ${name}`);

    if (name === '--app-id') values.appId = value;
    else if (name === '--target') values.target = value;
    else if (name === '--host') values.host = value;
    else if (name === '--port') values.port = Number(value);
    else if (name === '--customer-header') values.customerHeader = value.toLowerCase();
    else if (name === '--customer-id') values.customerId = value;

    index += 1;
  }

  if (!values.appId || !ID_PATTERN.test(values.appId)) {
    throw new Error('appId must match [a-z0-9-]+');
  }

  if (!Number.isInteger(values.port) || values.port < 1 || values.port > 65535) {
    throw new Error('--port must be an integer from 1 through 65535');
  }

  if (!/^[a-z0-9-]+$/.test(values.customerHeader)) {
    throw new Error('--customer-header must be a valid header token');
  }

  values.customerId = values.customerId || DEFAULT_CUSTOMER_ID;
  if (!ID_PATTERN.test(values.customerId)) {
    throw new Error('--customer-id must match [a-z0-9-]+');
  }

  return values;
}

function readJson(filename) {
  return JSON.parse(fs.readFileSync(filename, 'utf8'));
}

function readAppTarget(appId) {
  const appPath = path.join(APPS_DIR, appId, 'app.json');
  let config;

  try {
    config = readJson(appPath);
  } catch (error) {
    throw new Error(`Unable to read app configuration: ${error.message}`);
  }

  const target = config.url || config.baseUrl;
  if (typeof target !== 'string' || !target) {
    throw new Error('App configuration must define url or baseUrl');
  }

  try {
    new URL(target);
  } catch {
    throw new Error('App configuration contains an invalid target URL');
  }

  return target;
}

function getTarget(options) {
  const target = options.target || readAppTarget(options.appId);
  try {
    new URL(target);
  } catch {
    throw new Error('Target must be a valid URL');
  }
  return target;
}

function customerIdFromRequest(request, headerName, fallbackId) {
  const value = request.headers[headerName];
  if (typeof value === 'string' && ID_PATTERN.test(value)) return value;
  return fallbackId;
}

function readLayout(customerId, appId) {
  const layoutPath = path.join(LAYOUTS_DIR, customerId, `${appId}.json`);
  const fallbackPath = path.join(LAYOUTS_DIR, DEFAULT_CUSTOMER_ID, `${appId}.json`);

  let parsed;
  try {
    parsed = readJson(layoutPath);
  } catch (error) {
    if (error.code !== 'ENOENT' || customerId === DEFAULT_CUSTOMER_ID) {
      if (error.code === 'ENOENT') return null;
      throw new Error(`Unable to read layout: ${error.message}`);
    }
    try { parsed = readJson(fallbackPath); }
    catch (fallbackError) {
      if (fallbackError.code === 'ENOENT') return null;
      throw new Error(`Unable to read fallback layout: ${fallbackError.message}`);
    }
  }

  if (
    !parsed ||
    typeof parsed !== 'object' ||
    Array.isArray(parsed) ||
    typeof parsed.layout !== 'string' ||
    !ID_PATTERN.test(parsed.layout) ||
    !Number.isInteger(parsed.version) ||
    parsed.version < 1
  ) {
    throw new Error('Layout must contain a valid layout and positive version');
  }

  return parsed;
}

function readSkin(appId, layoutId) {
  const skinPath = path.join(SKINS_DIR, appId, `${layoutId}.css`);

  try {
    return fs.readFileSync(skinPath);
  } catch (error) {
    if (error.code === 'ENOENT') return null;
    throw new Error(`Unable to read skin: ${error.message}`);
  }
}

function stylesheetPath(appId, layout) {
  return `/_cs/${encodeURIComponent(appId)}/${encodeURIComponent(layout.layout)}.${layout.version}.css`;
}

function injectStylesheet(html, href) {
  const tag = `<link rel="stylesheet" href="${href}">`;
  const closingHead = /<\/head\s*>/i;
  return closingHead.test(html) ? html.replace(closingHead, `${tag}</head>`) : html;
}

function decodeBody(body, encoding) {
  switch (encoding) {
    case '':
    case 'identity':
      return body;
    case 'gzip':
      return zlib.gunzipSync(body);
    case 'br':
      return zlib.brotliDecompressSync(body);
    case 'deflate':
      return zlib.inflateSync(body);
    default:
      throw new Error(`Unsupported Content-Encoding: ${encoding}`);
  }
}

function encodeBody(body, encoding) {
  switch (encoding) {
    case '':
    case 'identity':
      return body;
    case 'gzip':
      return zlib.gzipSync(body);
    case 'br':
      return zlib.brotliCompressSync(body);
    case 'deflate':
      return zlib.deflateSync(body);
    default:
      throw new Error(`Unsupported Content-Encoding: ${encoding}`);
  }
}

function cleanResponseHeaders(headers) {
  const result = {};

  for (const [name, value] of Object.entries(headers)) {
    if (!HOP_BY_HOP_HEADERS.has(name.toLowerCase())) {
      result[name] = value;
    }
  }

  return result;
}

function cleanRequestHeaders(headers) {
  const result = {};

  for (const [name, value] of Object.entries(headers)) {
    if (!HOP_BY_HOP_HEADERS.has(name.toLowerCase())) {
      result[name] = value;
    }
  }

  return result;
}

function sendError(response, statusCode, message) {
  if (response.headersSent) {
    response.destroy();
    return;
  }

  const body = Buffer.from(message, 'utf8');
  response.writeHead(statusCode, {
    'Content-Type': 'text/plain; charset=utf-8',
    'Content-Length': String(body.length),
    'Cache-Control': 'no-store'
  });
  response.end(body);
}

function servePort(response) { try { const port=fs.readFileSync(PORT_FILE); response.writeHead(200, {'Content-Type':'application/javascript; charset=utf-8','Cache-Control':'no-store','Content-Length':String(port.length)}); response.end(port); } catch(error) { sendError(response,404,`Not found: ${error.message}\n`); } }
function escAttr(v) { return String(v).replace(/[&"<>]/g, c => ({"&":"&amp;","\"":"&quot;","<":"&lt;",">":"&gt;"}[c])); }
function injectHook(html, appId) { if (html.includes(HOOK_MARKER)) return html; const tag=`<script src="${PORT_ROUTE}" ${HOOK_MARKER}="1"${appId ? ` data-capability-app="${escAttr(appId)}"` : ''} defer></script>`; const re=/<\/head\s*>/i; return re.test(html)?html.replace(re,`${tag}</head>`):html; }

function serveSkin(response, request, options, layoutId, version) {
  if (!ID_PATTERN.test(layoutId) || !/^\d+$/.test(version)) {
    sendError(response, 404, 'Not found\n');
    return;
  }

  const customerId = customerIdFromRequest(request, options.customerHeader, options.customerId);
  const selected = readLayout(customerId, options.appId);

  if (
    !selected ||
    selected.layout !== layoutId ||
    String(selected.version) !== version
  ) {
    sendError(response, 404, 'Not found\n');
    return;
  }

  const skin = readSkin(options.appId, layoutId);

  if (!skin) {
    sendError(response, 404, 'Not found\n');
    return;
  }

  response.writeHead(200, {
    'Content-Type': 'text/css; charset=utf-8',
    'Cache-Control': 'no-store',
    'Content-Length': String(skin.length)
  });
  response.end(skin);
}

function proxyRequest(request, response, options, target) {
  const targetUrl = new URL(target);
  const transport = targetUrl.protocol === 'https:' ? https : http;

  const requestHeaders = cleanRequestHeaders(request.headers);
  // Keep the browser's Host so the app builds URLs for the public address; say where the request came from.
  requestHeaders['x-forwarded-host'] = request.headers.host || '';
  requestHeaders['x-forwarded-proto'] = request.socket.encrypted ? 'https' : 'http';
  delete requestHeaders[options.customerHeader];

  const upstreamOptions = {
    protocol: targetUrl.protocol,
    hostname: targetUrl.hostname,
    port: targetUrl.port || (targetUrl.protocol === 'https:' ? 443 : 80),
    method: request.method,
    path: new URL(request.url, `http://${request.headers.host || options.host}`).pathname +
      (new URL(request.url, `http://${request.headers.host || options.host}`).search || ''),
    headers: requestHeaders
  };

  const upstream = transport.request(upstreamOptions, upstreamResponse => {
    const chunks = [];

    upstreamResponse.on('data', chunk => chunks.push(chunk));

    upstreamResponse.once('error', error => {
      sendError(response, 502, `Upstream response error: ${error.message}\n`);
    });

    upstreamResponse.once('end', () => {
      try {
        const body = Buffer.concat(chunks);
        const headers = cleanResponseHeaders(upstreamResponse.headers);
        const contentType = String(headers['content-type'] || '');

        if (!HTML_PATTERN.test(contentType)) {
          response.writeHead(upstreamResponse.statusCode || 502, headers);
          response.end(body);
          return;
        }

        const encoding = String(headers['content-encoding'] || '').toLowerCase();
        const decoded = decodeBody(body, encoding);
        const html = decoded.toString('utf8');
        let layout = null;
        try { layout = readLayout(customerIdFromRequest(request, options.customerHeader, options.customerId), options.appId); } catch (error) { if (!HOOK_ALWAYS) throw error; }
        let injected = layout ? injectStylesheet(html, stylesheetPath(options.appId, layout)) : html;
        if (HOOK_ALWAYS || layout) injected = injectHook(injected, options.appId);

        const encoded = encodeBody(Buffer.from(injected, 'utf8'), encoding);

        delete headers['content-length'];
        delete headers['content-encoding'];

        if (encoding && encoding !== 'identity') {
          headers['content-encoding'] = encoding;
        }

        headers['content-length'] = String(encoded.length);

        response.writeHead(upstreamResponse.statusCode || 502, headers);
        response.end(encoded);
      } catch (error) {
        sendError(response, 502, `FAIL: ${error.message}\n`);
      }
    });
  });

  upstream.once('error', error => {
    sendError(response, 502, `Proxy error: ${error.message}\n`);
  });

  request.pipe(upstream);
}

function createServer(options) {
  const target = getTarget(options);

  const server = http.createServer((request, response) => {
    try {
      const requestUrl = new URL(
        request.url,
        `http://${request.headers.host || options.host}`
      );

      const skinPattern = new RegExp(
        `^/_cs/${options.appId}/([a-z0-9-]+)\\.(\\d+)\\.css$`
      );

      if (request.method === 'GET' && requestUrl.pathname === PORT_ROUTE) { servePort(response); return; }
      const capMatch = request.method === 'GET' && requestUrl.pathname.match(CAPS_ROUTE);
      if (capMatch) { try { const body = fs.readFileSync(path.join(CAPS_DIR, capMatch[1])); response.writeHead(200, {'Content-Type':'text/javascript; charset=utf-8','Cache-Control':'no-store','Content-Length':String(body.length)}); response.end(body); } catch { sendError(response, 404, 'Not found\n'); } return; }

      const match = requestUrl.pathname.match(skinPattern);

      if (match) {
        serveSkin(response, request, options, match[1], match[2]);
        return;
      }

      proxyRequest(request, response, options, target);
    } catch (error) {
      sendError(response, 400, `FAIL: ${error.message}\n`);
    }
  });

  server.on('error', error => {
    process.stderr.write(`${error.message}\n`);
    process.exitCode = 1;
  });

  server.listen(options.port, options.host, () => {
    process.stdout.write('PASS\n');
  });

  return server;
}

try {
  const options = parseArguments(process.argv.slice(2));
  createServer(options);
} catch (error) {
  process.stderr.write(`${error.message}\n`);
  process.stdout.write('FAIL\n');
  process.exitCode = 1;
}
