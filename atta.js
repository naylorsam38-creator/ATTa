#!/usr/bin/env node
'use strict';
// ATTa — numbered app intake and test pipeline.
//
//   node atta.js add <dir|git-url> [name]   intake: copies to apps/NNN-name
//   node atta.js run [NNN ...]              1 build → 2 serve → 3 proxy → 4 Playwright → 5 record
//   node atta.js results                    print results/RESULTS.md
//
// Optional per-app atta.json: { "build": "...", "start": "...", "static": "dist", "path": "/" }

const fs = require('fs');
const path = require('path');
const { execSync } = require('child_process');
const { createProxy } = require('./cp/proxy');
const { serveStatic, serveCommand, listen } = require('./lib/serve');
const { runChecks } = require('./lib/check');

const ROOT = __dirname;
const APPS = path.join(ROOT, 'apps');
const RESULTS = path.join(ROOT, 'results');
const SKIP = new Set(['node_modules', '.git']);

function numbered() {
  if (!fs.existsSync(APPS)) return [];
  return fs.readdirSync(APPS).filter((d) => /^\d{3}-/.test(d)).sort();
}

function slug(s) {
  return String(s).toLowerCase().replace(/\.git$/, '').replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '') || 'app';
}

function copyDir(src, dst) {
  fs.mkdirSync(dst, { recursive: true });
  for (const e of fs.readdirSync(src, { withFileTypes: true })) {
    if (SKIP.has(e.name)) continue;
    const s = path.join(src, e.name), d = path.join(dst, e.name);
    if (e.isDirectory()) copyDir(s, d);
    else if (e.isFile()) fs.copyFileSync(s, d);
  }
}

function add(source, name) {
  if (!source) throw new Error('usage: node atta.js add <dir|git-url> [name]');
  const last = numbered().pop();
  const n = String(last ? Number(last.slice(0, 3)) + 1 : 1).padStart(3, '0');
  const id = `${n}-${slug(name || path.basename(source.replace(/\/$/, '')))}`;
  const dst = path.join(APPS, id);
  if (/^(https?:|git@)/.test(source)) {
    execSync(`git clone --depth 1 ${JSON.stringify(source)} ${JSON.stringify(dst)}`, { stdio: 'inherit' });
    fs.rmSync(path.join(dst, '.git'), { recursive: true, force: true });
  } else {
    if (!fs.statSync(source).isDirectory()) throw new Error(`not a directory: ${source}`);
    copyDir(path.resolve(source), dst);
  }
  console.log(`added ${id}`);
  return id;
}

function readJson(f) {
  try { return JSON.parse(fs.readFileSync(f, 'utf8')); } catch (_) { return null; }
}

function findStaticDir(dir, hint) {
  const candidates = hint ? [hint] : ['dist', 'build', 'out', 'public', '.'];
  for (const c of candidates) {
    if (fs.existsSync(path.join(dir, c, 'index.html'))) return path.join(dir, c);
  }
  return null;
}

async function runOne(id) {
  const dir = path.join(APPS, id);
  const out = path.join(RESULTS, id);
  fs.rmSync(out, { recursive: true, force: true });
  fs.mkdirSync(out, { recursive: true });
  const logFile = path.join(out, 'build.log');
  const log = (s) => fs.appendFileSync(logFile, s);
  fs.writeFileSync(logFile, '');

  const cfg = readJson(path.join(dir, 'atta.json')) || {};
  const pkg = readJson(path.join(dir, 'package.json'));
  const scripts = (pkg && pkg.scripts) || {};
  const stages = [];
  const result = { app: id, number: id.slice(0, 3), startedAt: new Date().toISOString(), stages, checks: [] };
  const stage = (n, name, status, detail) => stages.push({ stage: n, name, status, detail });
  const sh = (cmd) => {
    log(`\n$ ${cmd}\n`);
    log(execSync(cmd, { cwd: dir, stdio: 'pipe', env: { ...process.env, CI: '1' }, maxBuffer: 64 << 20 }).toString());
  };
  const stops = [];

  try {
    // 1 — build
    const buildCmd = cfg.build || (scripts.build ? 'npm run build' : null);
    try {
      if (pkg) sh(fs.existsSync(path.join(dir, 'package-lock.json')) ? 'npm ci' : 'npm install');
      if (buildCmd) sh(buildCmd);
      stage(1, 'build', 'pass', buildCmd || (pkg ? 'install only' : 'nothing to build'));
    } catch (e) {
      log(String(e.stdout || '') + String(e.stderr || ''));
      stage(1, 'build', 'fail', `failed: ${buildCmd || 'install'} (see build.log)`);
      throw e;
    }

    // 2 — serve
    let app;
    try {
      const staticDir = cfg.start ? null : findStaticDir(dir, cfg.static);
      if (staticDir) app = await serveStatic(staticDir);
      else if (cfg.start || scripts.start) app = await serveCommand(cfg.start || 'npm start', dir, log);
      else throw new Error('no index.html (dist/build/out/public/root) and no start script');
      stops.push(app.stop);
      stage(2, 'serve', 'pass', staticDir ? `static ${path.relative(dir, staticDir) || '.'}` : cfg.start || 'npm start');
    } catch (e) {
      stage(2, 'serve', 'fail', e.message);
      throw e;
    }

    // 3 — proxy (proxy.js → port.js → mover.js)
    const proxy = createProxy({ target: app.url, appId: id });
    const proxyPort = await listen(proxy);
    stops.push(() => proxy.close());
    const proxyUrl = `http://127.0.0.1:${proxyPort}`;
    stage(3, 'proxy', 'pass', `${proxyUrl} → ${app.url}`);

    // 4 — Playwright
    try {
      const r = await runChecks({ proxyUrl, appId: id, pagePath: cfg.path || '/', screenshot: path.join(out, 'screenshot.png') });
      result.checks = r.checks;
      result.consoleErrors = r.consoleErrors;
      const failed = r.checks.filter((c) => c.status === 'fail').length;
      stage(4, 'playwright', failed ? 'fail' : 'pass', `${r.checks.length - failed}/${r.checks.length} not failing`);
    } catch (e) {
      stage(4, 'playwright', 'fail', e.message);
    }
  } catch (_) {
    // stage already recorded
  } finally {
    stops.reverse().forEach((s) => { try { s(); } catch (_) {} });
  }

  const statuses = [...stages.map((s) => s.status), ...result.checks.map((c) => c.status)];
  result.verdict = statuses.includes('fail') ? 'FAIL' : statuses.some((s) => s === 'warn' || s === 'skip') ? 'WARN' : 'PASS';
  result.finishedAt = new Date().toISOString();
  // 5 — record
  fs.writeFileSync(path.join(out, 'result.json'), JSON.stringify(result, null, 2) + '\n');
  fs.writeFileSync(path.join(out, 'REPORT.md'), report(result));
  console.log(`${id}: ${result.verdict}`);
  return result;
}

function report(r) {
  const lines = [`# ${r.app} — ${r.verdict}`, '', `Run: ${r.startedAt}`, '', '## Stages', '',
    '| # | Stage | Status | Detail |', '|---|---|---|---|'];
  r.stages.forEach((s) => lines.push(`| ${s.stage} | ${s.name} | ${s.status.toUpperCase()} | ${s.detail} |`));
  if (r.checks.length) {
    lines.push('', '## Playwright checks', '', '| # | Check | Status | Detail |', '|---|---|---|---|');
    r.checks.forEach((c) => lines.push(`| ${c.id} | ${c.name} | ${c.status.toUpperCase()} | ${String(c.detail).replace(/\|/g, '\\|')} |`));
  }
  if (r.consoleErrors && r.consoleErrors.length) {
    lines.push('', '## Console errors', '', ...r.consoleErrors.slice(0, 20).map((e) => '- ' + e));
  }
  return lines.join('\n') + '\n';
}

function summary() {
  const rows = numbered()
    .map((id) => readJson(path.join(RESULTS, id, 'result.json')))
    .filter(Boolean);
  const lines = ['# ATTa results', '', '| # | App | Verdict | Build | Serve | Proxy | Playwright | Checks | Run |',
    '|---|---|---|---|---|---|---|---|---|'];
  for (const r of rows) {
    const st = (n) => (r.stages.find((s) => s.stage === n) || { status: '—' }).status.toUpperCase();
    const checks = r.checks.map((c) => `${c.id}:${c.status[0].toUpperCase()}`).join(' ') || '—';
    lines.push(`| ${r.number} | [${r.app}](${r.app}/REPORT.md) | **${r.verdict}** | ${st(1)} | ${st(2)} | ${st(3)} | ${st(4)} | ${checks} | ${r.startedAt.slice(0, 16).replace('T', ' ')} |`);
  }
  lines.push('', 'Checks: C1 loads · C2 proxy hook before </head> · C3 port.js live · C4 mover slot in drawer · ' +
    'C5 buttons numbered · C6 move+reset · C7 drawer opens · C8 no page errors. P=pass F=fail W=warn S=skip.');
  const md = lines.join('\n') + '\n';
  fs.mkdirSync(RESULTS, { recursive: true });
  fs.writeFileSync(path.join(RESULTS, 'RESULTS.md'), md);
  return md;
}

async function main() {
  const [cmd, ...args] = process.argv.slice(2);
  if (cmd === 'add') {
    add(args[0], args[1]);
  } else if (cmd === 'run') {
    const all = numbered();
    const ids = args.length ? all.filter((id) => args.some((a) => id === a || id.startsWith(a.padStart(3, '0') + '-'))) : all;
    if (!ids.length) throw new Error('no apps to run — add one with: node atta.js add <dir|git-url>');
    let failed = 0;
    for (const id of ids) if ((await runOne(id)).verdict === 'FAIL') failed++;
    console.log('\n' + summary());
    process.exitCode = failed ? 1 : 0;
  } else if (cmd === 'results') {
    console.log(summary());
  } else {
    console.log('usage: node atta.js add <dir|git-url> [name] | run [NNN ...] | results');
  }
}

main().catch((e) => { console.error(e.message); process.exit(1); });
