'use strict';
// Deploy-ready bundle for one numbered app, then proof that the bundle works.
//
// dist/NNN-name/                      (also zipped to dist/NNN-name.zip)
//   docker-compose.yml                Coolify "Docker Compose" build pack; `proxy` is the only public service
//   docker-compose.local.yml          plain Docker / AWS host: publishes the proxy on ${PROXY_PORT:-8080}
//   capability/                       proxy image: ui-bridge/proxy.js, layouts, skins + capability-port/
//   app/                              the app source exactly as received (build outputs/deps left out)
//   …                                 the app's own Dockerfiles/config from apps/NNN-name/deploy/
//
// verify(): docker compose up --build, then the same Playwright checks, run against the containers.
const fs = require('fs');
const path = require('path');
const { execSync, spawnSync } = require('child_process');
const { freePort } = require('./serve');
const { runChecks } = require('./check');

const ROOT = path.join(__dirname, '..');
const SKIP = new Set(['node_modules', 'vendor', '.venv', '.output', '.nuxt', '.git', '.intake', '__pycache__', '.pytest_cache']);

function copyTree(src, dst, skip = SKIP) {
  fs.mkdirSync(dst, { recursive: true });
  for (const e of fs.readdirSync(src, { withFileTypes: true })) {
    if (skip.has(e.name)) continue;
    const s = path.join(src, e.name), d = path.join(dst, e.name);
    if (e.isDirectory()) copyTree(s, d, skip);
    else if (e.isFile()) fs.copyFileSync(s, d);
    else if (e.isSymbolicLink()) { try { fs.symlinkSync(fs.readlinkSync(s), d); } catch (_) {} }
  }
}

function customersFor(appId) {
  const dir = path.join(ROOT, 'ui-bridge', 'layouts');
  const map = {};
  if (!fs.existsSync(dir)) return map;
  for (const c of fs.readdirSync(dir)) {
    const f = path.join(dir, c, `${appId}.json`);
    if (fs.existsSync(f)) map[c] = JSON.parse(fs.readFileSync(f, 'utf8')).layout;
  }
  return map;
}

function build(id, { src, recipeDir, intake }) {
  const dist = path.join(ROOT, 'dist', id);
  fs.rmSync(dist, { recursive: true, force: true });
  fs.mkdirSync(dist, { recursive: true });

  // The app's deploy recipe: compose files, Dockerfiles, config.
  copyTree(recipeDir, dist, new Set(['deploy.json']));

  // Shared recipes (e.g. 002 and 007) name the app id as ${ATTA_APP_ID}; bake the real id in.
  for (const f of fs.readdirSync(dist)) {
    if (/^docker-compose.*\.ya?ml$/.test(f)) {
      const p = path.join(dist, f);
      fs.writeFileSync(p, fs.readFileSync(p, 'utf8').replace(/\$\{ATTA_APP_ID\}/g, id));
    }
  }

  // The app, exactly as received: re-extracted from the original zip, because the test pipeline's
  // build steps change src/ in place (.env, keys, lockfiles, build output) and none of that may ship.
  const appDir = path.join(dist, 'app');
  if (intake && intake.sourcePath && /\.zip$/i.test(intake.sourcePath) && fs.existsSync(intake.sourcePath)) {
    const tmp = path.join(ROOT, 'dist', `.${id}-unzip`);
    fs.rmSync(tmp, { recursive: true, force: true });
    execSync(`unzip -q ${JSON.stringify(intake.sourcePath)} -d ${JSON.stringify(tmp)}`);
    const top = fs.readdirSync(tmp).filter((d) => !d.startsWith('__MACOSX') && d !== 'out');
    const root = top.length === 1 && fs.statSync(path.join(tmp, top[0])).isDirectory() ? path.join(tmp, top[0]) : tmp;
    // The capability bundle the zip carries (out/) is what capability/ integrates; don't ship it twice.
    fs.rmSync(path.join(root, 'out'), { recursive: true, force: true });
    fs.renameSync(root, appDir);
    fs.rmSync(tmp, { recursive: true, force: true });
    execSync(`chmod -R a+rX ${JSON.stringify(appDir)}`);
  } else {
    copyTree(src, appDir, new Set([...SKIP, '.env']));
  }
  // Some recipes add files into the app tree (e.g. a Dockerfile the upstream doesn't ship).
  const overlay = path.join(recipeDir, 'app-overlay');
  if (fs.existsSync(overlay)) { copyTree(overlay, path.join(dist, 'app')); fs.rmSync(path.join(dist, 'app-overlay'), { recursive: true, force: true }); }

  // The capability layer: proxy + Port + caps + this app's skins, in the layout proxy.js expects.
  const cap = path.join(dist, 'capability');
  fs.mkdirSync(path.join(cap, 'ui-bridge'), { recursive: true });
  fs.copyFileSync(path.join(ROOT, 'deploy', 'capability', 'Dockerfile'), path.join(cap, 'Dockerfile'));
  fs.copyFileSync(path.join(ROOT, 'ui-bridge', 'proxy.js'), path.join(cap, 'ui-bridge', 'proxy.js'));
  copyTree(path.join(ROOT, 'capability-port'), path.join(cap, 'capability-port'));
  const skins = customersFor(id);
  for (const customer of Object.keys(skins)) {
    fs.mkdirSync(path.join(cap, 'ui-bridge', 'layouts', customer), { recursive: true });
    fs.copyFileSync(path.join(ROOT, 'ui-bridge', 'layouts', customer, `${id}.json`), path.join(cap, 'ui-bridge', 'layouts', customer, `${id}.json`));
  }
  if (fs.existsSync(path.join(ROOT, 'ui-bridge', 'skins', id))) copyTree(path.join(ROOT, 'ui-bridge', 'skins', id), path.join(cap, 'ui-bridge', 'skins', id));

  const zip = path.join(ROOT, 'dist', `${id}.zip`);
  fs.rmSync(zip, { force: true });
  execSync(`zip -qr ${JSON.stringify(zip)} ${JSON.stringify(id)}`, { cwd: path.join(ROOT, 'dist') });
  return { dist, zip, skins };
}

function waitHttp(url, timeoutMs) {
  const http = require('http');
  const end = Date.now() + timeoutMs;
  return new Promise((resolve, reject) => {
    const tick = () => {
      http.get(url, (r) => { r.resume(); resolve(r.statusCode); }).on('error', () => {
        if (Date.now() > end) reject(new Error(`no answer on ${url} after ${timeoutMs / 1000}s`));
        else setTimeout(tick, 2000);
      });
    };
    tick();
  });
}

// Verification only (never shipped): this sandbox reaches the internet through a local HTTPS proxy
// with its own CA. Build every image from a temporary Dockerfile.verify that trusts that CA, on the
// host network, with the proxy set. The bundle and its zip keep the untouched Dockerfiles.
const SANDBOX_CA = '/root/.ccr/ca-bundle.crt';
function sandboxBuildOverride(dist, project, env) {
  const proxy = process.env.HTTPS_PROXY || process.env.https_proxy;
  if (!proxy || !fs.existsSync(SANDBOX_CA)) return { files: [], cleanup: () => {} };
  const cfg = JSON.parse(execSync(`docker compose -p ${project} -f docker-compose.yml -f docker-compose.local.yml config --format json`,
    { cwd: dist, env, maxBuffer: 64 << 20 }).toString());
  const created = [];
  const services = {};
  for (const [name, svc] of Object.entries(cfg.services || {})) {
    if (!svc.build) continue;
    const ctx = svc.build.context;
    const df = path.join(ctx, svc.build.dockerfile || 'Dockerfile');
    const lines = fs.readFileSync(df, 'utf8').split('\n');
    const outLines = [];
    for (const line of lines) {
      outLines.push(line);
      if (/^\s*FROM\s/i.test(line)) {
        outLines.push('COPY --from=sandboxca ca-bundle.crt /etc/ssl/certs/ca-certificates.crt',
          'ENV SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt NODE_EXTRA_CA_CERTS=/etc/ssl/certs/ca-certificates.crt ' +
          'REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt PIP_CERT=/etc/ssl/certs/ca-certificates.crt ' +
          'UV_NATIVE_TLS=1 CURL_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt GIT_SSL_CAINFO=/etc/ssl/certs/ca-certificates.crt');
      }
    }
    const verifyDf = path.join(path.dirname(df), 'Dockerfile.verify');
    fs.writeFileSync(verifyDf, outLines.join('\n'));
    created.push(verifyDf);
    if (fs.existsSync(df + '.dockerignore')) {   // per-Dockerfile ignore file must follow the renamed Dockerfile
      fs.copyFileSync(df + '.dockerignore', verifyDf + '.dockerignore');
      created.push(verifyDf + '.dockerignore');
    }
    services[name] = { build: { context: ctx, dockerfile: path.relative(ctx, verifyDf), network: 'host',
      additional_contexts: { sandboxca: path.dirname(SANDBOX_CA) },
      args: { HTTPS_PROXY: proxy, HTTP_PROXY: proxy, https_proxy: proxy, http_proxy: proxy, NO_PROXY: 'localhost,127.0.0.1', no_proxy: 'localhost,127.0.0.1' } } };
  }
  const override = path.join(dist, 'docker-compose.verify.json');
  fs.writeFileSync(override, JSON.stringify({ services }, null, 2));
  created.push(override);
  return { files: ['-f', 'docker-compose.verify.json'], cleanup: () => created.forEach((f) => fs.rmSync(f, { force: true })) };
}

async function verify(id, { dist, skins, pagePath = '/', timeout = 600, log }) {
  const port = await freePort();
  const project = 'atta-' + id.replace(/[^a-z0-9]/g, '');
  const env = { ...process.env, PROXY_PORT: String(port) };
  const sandbox = sandboxBuildOverride(dist, project, env);
  const compose = (args, ms) => {
    const r = spawnSync('docker', ['compose', '-p', project, '-f', 'docker-compose.yml', '-f', 'docker-compose.local.yml', ...sandbox.files, ...args],
      { cwd: dist, env, encoding: 'utf8', timeout: ms, maxBuffer: 256 << 20 });
    log(`\n$ docker compose ${args.join(' ')}\n${r.stdout || ''}${r.stderr || ''}`);
    return r;
  };
  const t0 = Date.now();
  try {
    const up = compose(['up', '-d', '--build'], timeout * 1000);
    if (up.status !== 0) return { ok: false, detail: `docker compose up failed (exit ${up.status}${up.error ? ', ' + up.error.message : ''}); see deploy.log` };
    const proxyUrl = `http://127.0.0.1:${port}`;
    try { await waitHttp(proxyUrl + pagePath, 180000); }
    catch (e) { compose(['logs', '--tail', '80'], 60000); return { ok: false, detail: e.message }; }
    await new Promise((r) => setTimeout(r, 3000));
    const r = await runChecks({ proxyUrl, appUrl: null, appId: id, pagePath, skins });
    return { ok: true, checks: r.checks, seconds: Math.round((Date.now() - t0) / 1000), proxyUrl };
  } finally {
    compose(['down', '-v', '--remove-orphans'], 300000);
    sandbox.cleanup();
  }
}

module.exports = { build, verify };
