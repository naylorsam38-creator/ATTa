#!/usr/bin/env node
'use strict';
// ATTa — numbered app intake and test pipeline for the Capability Port.
//
//   node atta.js add <zip|dir|git-url> [name]   intake → apps/NNN-name/src
//   node atta.js run [NNN ...]                  0 intake → 1 build → 2 serve → 3 proxy → 4 Playwright → 5 record
//   node atta.js package [NNN ...]              6 deploy bundle → docker compose up → same checks against the containers
//   node atta.js results                        rebuild results/RESULTS.md
//
// Per-app recipe apps/NNN-name/atta.json:
//   { "setup": ["cmd", ...],          run in src/, in order (stage 1)
//     "start": "cmd",                  long-running server; gets PORT (stage 2)
//     "static": "dir",                 or: serve this directory of src/ instead of "start"
//     "env": { "K": "V" },             extra env for setup + start ($PORT, $SRC and $DATA are expanded)
//     "path": "/login",                page the checks open
//     "timeout": 120 }                 seconds to wait for the server to answer

const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const { execSync, spawn } = require('child_process');
const { serveStatic, serveCommand, freePort } = require('./lib/serve');
const { runChecks } = require('./lib/check');
const { syncSkins } = require('./lib/skins');
const { recordDemo } = require('./lib/demo');
const pkg = require('./lib/package');

const ROOT = __dirname;
const APPS = path.join(ROOT, 'apps');
const RESULTS = path.join(ROOT, 'results');
const PROXY = path.join(ROOT, 'ui-bridge', 'proxy.js');
const CANON = {
  'ui-bridge/proxy.js': PROXY,
  'capability-port/port.js': path.join(ROOT, 'capability-port', 'port.js'),
  'capability-port/caps/mover.mjs': path.join(ROOT, 'capability-port', 'caps', 'mover.mjs'),
};
const SKIP = new Set(['node_modules', '.git']);

const md5 = (f) => crypto.createHash('md5').update(fs.readFileSync(f)).digest('hex');
const readJson = (f) => { try { return JSON.parse(fs.readFileSync(f, 'utf8')); } catch (_) { return null; } };

function numbered() {
  if (!fs.existsSync(APPS)) return [];
  return fs.readdirSync(APPS).filter((d) => /^\d{3}-/.test(d)).sort();
}

// "39a18863-Alexandrie-main-capability-patched.zip" → "alexandrie"
function slug(s) {
  return String(s).toLowerCase()
    .replace(/\.(zip|git)$/, '')
    .replace(/^[0-9a-f]{8}-/, '')
    .replace(/-capability-patched$/, '')
    .replace(/_\d+_$/, '')
    .replace(/-(main|master|dev)$/, '')
    .replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '') || 'app';
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

// Compare a bundled out/ folder (the capability patch) with the canonical files.
function checkBundle(outDir) {
  const files = {};
  for (const [rel, canon] of Object.entries(CANON)) {
    const f = path.join(outDir, rel);
    files[rel] = !fs.existsSync(f) ? 'missing' : md5(f) === md5(canon) ? 'match' : 'differs';
  }
  return files;
}

function add(source, name) {
  if (!source) throw new Error('usage: node atta.js add <zip|dir|git-url> [name]');
  // Numbers are permanent: the exact same zip again keeps the number it already has.
  if (/\.zip$/i.test(source)) {
    const sum = md5(source);
    const dup = numbered().find((id) => (readJson(path.join(APPS, id, 'intake.json')) || {}).md5 === sum);
    if (dup) {
      const f = path.join(APPS, dup, 'intake.json');
      const rec = readJson(f);
      rec.duplicates = [...new Set([...(rec.duplicates || []), path.basename(source)])];
      fs.writeFileSync(f, JSON.stringify(rec, null, 2) + '\n');
      console.log(`${path.basename(source)} is byte-identical to ${dup} — kept as ${dup}, no new number`);
      return dup;
    }
  }
  const last = numbered().pop();
  const n = String(last ? Number(last.slice(0, 3)) + 1 : 1).padStart(3, '0');
  const id = `${n}-${slug(name || path.basename(source.replace(/\/$/, '')))}`;
  const dir = path.join(APPS, id);
  const src = path.join(dir, 'src');
  const intake = { id, source: path.basename(source), sourcePath: path.resolve(source), addedAt: new Date().toISOString(), bundle: null };
  fs.mkdirSync(dir, { recursive: true });

  if (/^(https?:|git@)/.test(source)) {
    execSync(`git clone --depth 1 ${JSON.stringify(source)} ${JSON.stringify(src)}`, { stdio: 'inherit' });
  } else if (/\.zip$/i.test(source)) {
    intake.md5 = md5(source);
    const tmp = path.join(dir, '.intake');
    fs.rmSync(tmp, { recursive: true, force: true });
    execSync(`unzip -q ${JSON.stringify(source)} -d ${JSON.stringify(tmp)}`);
    const top = fs.readdirSync(tmp).filter((d) => !d.startsWith('__MACOSX'));
    const app = top.filter((d) => d !== 'out');
    const appDir = app.length === 1 && fs.statSync(path.join(tmp, app[0])).isDirectory() ? path.join(tmp, app[0]) : tmp;
    // The capability bundle ships either beside the app (zip/out) or inside it (zip/app/out).
    const outDir = [path.join(tmp, 'out'), path.join(appDir, 'out')].find((d) => fs.existsSync(path.join(d, 'capability-port')));
    if (outDir) intake.bundle = checkBundle(outDir);
    fs.renameSync(appDir, src);
    fs.rmSync(tmp, { recursive: true, force: true });
  } else {
    copyDir(path.resolve(source), src);
    if (fs.existsSync(path.join(source, 'out'))) intake.bundle = checkBundle(path.join(source, 'out'));
  }
  // Zips often carry owner-only modes; apps that drop root privileges must still read their code.
  execSync(`chmod -R a+rX ${JSON.stringify(src)}`);
  fs.writeFileSync(path.join(dir, 'intake.json'), JSON.stringify(intake, null, 2) + '\n');
  console.log(`added ${id}` + (intake.bundle ? ` (bundle: ${JSON.stringify(intake.bundle)})` : ''));
  return id;
}

function startProxy(appId, target) {
  return freePort().then((port) => new Promise((resolve, reject) => {
    const p = spawn(process.execPath, [PROXY, '--app-id', appId, '--target', target, '--port', String(port)]);
    let out = '';
    const done = (ok, msg) => { clearTimeout(t); ok ? resolve({ url: `http://127.0.0.1:${port}`, stop: () => p.kill() }) : (p.kill(), reject(new Error(msg))); };
    const t = setTimeout(() => done(false, 'proxy did not report PASS within 10s'), 10000);
    p.stdout.on('data', (d) => { out += d; if (/PASS/.test(out)) done(true); else if (/FAIL/.test(out)) done(false, out.trim()); });
    p.stderr.on('data', (d) => { out += d; });
    p.on('exit', (c) => done(false, `proxy exited ${c}: ${out.trim()}`));
  }));
}

async function runOne(id) {
  const dir = path.join(APPS, id);
  const src = path.join(dir, 'src');
  const out = path.join(RESULTS, id);
  // Replace this stage's own outputs only; the deploy stage's deploy.json / DEPLOY.md / deploy.log stay.
  fs.mkdirSync(out, { recursive: true });
  for (const f of ['REPORT.md', 'result.json', 'build.log', 'screenshot-moved.png', 'screenshot-drawer.png', 'demo.mp4', 'demo.webm']) {
    fs.rmSync(path.join(out, f), { force: true });
  }
  const logFile = path.join(out, 'build.log');
  fs.writeFileSync(logFile, '');
  const log = (s) => fs.appendFileSync(logFile, s);

  const cfg = readJson(path.join(dir, 'atta.json')) || {};
  const intake = readJson(path.join(dir, 'intake.json')) || {};
  const data = path.join(dir, 'data');
  fs.mkdirSync(data, { recursive: true });
  const expand = (s, port) => String(s).replace(/\$SRC/g, src).replace(/\$DATA/g, data).replace(/\$PORT/g, port || '');
  const envFor = (port) => {
    const e = { ...process.env, CI: '1', PORT: String(port || '') };
    for (const [k, v] of Object.entries(cfg.env || {})) e[k] = expand(v, port);
    return e;
  };

  const stages = [];
  const result = { app: id, number: id.slice(0, 3), startedAt: new Date().toISOString(), stages, checks: [] };
  const stage = (n, name, status, detail) => stages.push({ stage: n, name, status, detail });
  const stops = [];

  try {
    // 0 — intake: the bundled capability patch is the canonical proxy/port/mover
    if (intake.bundle) {
      const bad = Object.entries(intake.bundle).filter(([, v]) => v !== 'match');
      stage(0, 'intake', bad.length ? 'warn' : 'pass',
        bad.length ? bad.map(([k, v]) => `${k} ${v}`).join(', ') : 'bundled proxy.js, port.js, mover.mjs match canonical');
    } else {
      stage(0, 'intake', 'pass', 'no bundle shipped; canonical files used');
    }

    // 1 — build
    const t0 = Date.now();
    try {
      for (const cmd of cfg.setup || []) {
        const c = expand(cmd);
        log(`\n$ ${c}\n`);
        log(execSync(c, { cwd: src, env: envFor(), stdio: 'pipe', maxBuffer: 256 << 20, shell: '/bin/bash' }).toString());
      }
      stage(1, 'build', 'pass', (cfg.setup || []).length ? `${cfg.setup.length} step(s), ${Math.round((Date.now() - t0) / 1000)}s` : 'nothing to build');
    } catch (e) {
      log(String(e.stdout || '') + String(e.stderr || ''));
      stage(1, 'build', 'fail', `failed: ${String(e.cmd || e.message).slice(0, 160)} (see build.log)`);
      throw e;
    }

    // Library (no web UI): its own test suite is the check; proxy/Port stages don't apply.
    if (cfg.kind === 'library') {
      for (const n of [2, 3]) stage(n, n === 2 ? 'serve' : 'proxy', 'n/a', 'no web UI');
      let out = '', ok = true;
      try { out = execSync(expand(cfg.test), { cwd: src, env: envFor(), stdio: 'pipe', shell: '/bin/bash' }).toString(); }
      catch (e) { ok = false; out = String(e.stdout || '') + String(e.stderr || ''); }
      // unittest writes its summary to stderr; capture both.
      try { out += execSync(expand(cfg.test) + ' 2>&1 >/dev/null | tail -n 3', { cwd: src, env: envFor(), shell: '/bin/bash' }).toString(); } catch (_) {}
      log(`\n$ ${cfg.test}\n${out}`);
      const ran = (out.match(/Ran (\d+) tests?/) || [])[1];
      const failures = (out.match(/FAILED \(([^)]*)\)/) || [])[1];
      result.checks = [{ id: 'T1', name: 'Unit tests pass', status: ok && !failures ? 'pass' : 'fail',
        detail: ran ? `${ran} tests ran, ${failures ? 'FAILED (' + failures + ')' : 'all OK'}` : 'test run did not report a count' }];
      stage(4, 'tests', result.checks[0].status, result.checks[0].detail);
      throw null; // skip to record
    }

    // 2 — serve
    let app;
    try {
      if (cfg.static) app = await serveStatic(path.join(src, cfg.static));
      else if (cfg.start) {
        const port = await freePort();
        app = await serveCommand(expand(cfg.start, port), src, log, (cfg.timeout || 120) * 1000, port, envFor(port));
      } else throw new Error('recipe has neither "start" nor "static"');
      stops.push(app.stop);
      stage(2, 'serve', 'pass', `${cfg.static ? 'static ' + cfg.static : cfg.start} → ${app.url}`);
    } catch (e) {
      stage(2, 'serve', 'fail', e.message);
      throw e;
    }

    // 3 — proxy (ui-bridge/proxy.js, spawned exactly as it runs in production)
    let proxy;
    try {
      proxy = await startProxy(id, app.url);
      stops.push(proxy.stop);
      stage(3, 'proxy', 'pass', `${proxy.url} → ${app.url}`);
    } catch (e) {
      stage(3, 'proxy', 'fail', e.message);
      throw e;
    }

    // 4 — Playwright
    try {
      const skins = syncSkins(id);
      const r = await runChecks({ proxyUrl: proxy.url, appUrl: app.url, appId: id, pagePath: cfg.path || '/', outDir: out, skins });
      result.checks = r.checks;
      result.consoleErrors = r.consoleErrors;
      result.pageErrors = r.pageErrors;
      const failed = r.checks.filter((c) => c.status === 'fail').length;
      stage(4, 'playwright', failed ? 'fail' : 'pass', `${r.checks.length - failed}/${r.checks.length} not failing`);

      // 5 — demo video: the Port UI, mover drag + rename, sticky note, each customer skin
      if ((r.checks.find((c) => c.id === 'C3') || {}).status === 'pass') {
        try {
          const v = await recordDemo({ proxyUrl: proxy.url, appId: id, pagePath: cfg.path || '/', skins, outDir: out });
          result.video = { steps: v.steps, files: [v.mp4, v.webm].filter(Boolean).map((f) => path.basename(f)) };
          stage(5, 'video', 'pass', `${v.steps.length} steps → ${result.video.files.join(', ')}`);
        } catch (e) {
          stage(5, 'video', 'warn', 'demo recording stopped: ' + e.message.split('\n')[0].slice(0, 160));
        }
      } else {
        stage(5, 'video', 'skip', 'Port not running in the page, nothing to show');
      }
    } catch (e) {
      stage(4, 'playwright', 'fail', e.message.split('\n')[0]);
    }
  } catch (_) {
    // already recorded as a stage
  } finally {
    stops.reverse().forEach((s) => { try { s(); } catch (_) {} });
  }

  const statuses = [...stages.map((s) => s.status), ...result.checks.map((c) => c.status)].filter((x) => x !== 'n/a');
  result.verdict = statuses.includes('fail') ? 'FAIL' : statuses.some((s) => s === 'warn' || s === 'skip') ? 'WARN' : 'PASS';
  result.finishedAt = new Date().toISOString();
  // 5 — record
  fs.writeFileSync(path.join(out, 'result.json'), JSON.stringify(result, null, 2) + '\n');
  fs.writeFileSync(path.join(out, 'REPORT.md'), report(result, cfg));
  console.log(`${id}: ${result.verdict}`);
  return result;
}

// 6 — package: deploy-ready bundle (dist/NNN-name + .zip), then docker compose up and the same checks against it.
async function packageOne(id) {
  const dir = path.join(APPS, id);
  const recipeDir = path.join(dir, 'deploy');
  const out = path.join(RESULTS, id);
  fs.mkdirSync(out, { recursive: true });
  if (!fs.existsSync(recipeDir)) { console.log(`${id}: no deploy recipe (apps/${id}/deploy/)`); return null; }
  const cfg = readJson(path.join(recipeDir, 'deploy.json')) || {};
  const logFile = path.join(out, 'deploy.log');
  fs.writeFileSync(logFile, '');
  const log = (s) => fs.appendFileSync(logFile, s);
  syncSkins(id);
  const b = pkg.build(id, { src: path.join(dir, 'src'), recipeDir, intake: readJson(path.join(dir, 'intake.json')) || {} });
  const v = await pkg.verify(id, { dist: b.dist, skins: b.skins, pagePath: cfg.path || '/', timeout: cfg.timeout || 900, log });
  const failed = v.checks ? v.checks.filter((c) => c.status === 'fail') : [];
  const deploy = {
    at: new Date().toISOString(),
    bundle: `dist/${id}/`, zip: `dist/${id}.zip`, zipBytes: fs.statSync(b.zip).size,
    status: !v.ok ? 'fail' : failed.length ? 'fail' : v.checks.some((c) => c.status === 'warn' || c.status === 'skip') ? 'warn' : 'pass',
    detail: v.ok ? `containers up in ${v.seconds}s; ${v.checks.length - failed.length}/${v.checks.length} checks not failing` : v.detail,
    checks: v.checks || [], note: cfg.note || '',
  };
  fs.writeFileSync(path.join(out, 'deploy.json'), JSON.stringify(deploy, null, 2) + '\n');
  const lines = [`# ${id} — deploy bundle ${deploy.status.toUpperCase()}`, '', `Built: ${deploy.at}`, '',
    `Bundle: \`${deploy.bundle}\` · zip: \`${deploy.zip}\` (${(deploy.zipBytes / 1048576).toFixed(1)} MB)`, ''];
  if (cfg.note) lines.push(`> ${cfg.note}`, '');
  lines.push(`Verification: ${deploy.detail}`, '');
  if (deploy.checks.length) {
    lines.push('Same Playwright checks, run against the containers (proxy container → app container):', '',
      '| # | Check | Status | Detail |', '|---|---|---|---|');
    deploy.checks.forEach((c) => lines.push(`| ${c.id} | ${c.name} | ${c.status.toUpperCase()} | ${cell(c.detail)} |`));
  }
  fs.writeFileSync(path.join(out, 'DEPLOY.md'), lines.join('\n') + '\n');
  console.log(`${id}: deploy ${deploy.status.toUpperCase()} — ${deploy.detail}`);
  return deploy;
}

const cell = (s) => String(s).replace(/\|/g, '\\|').replace(/\n/g, ' ');

function report(r, cfg) {
  const lines = [`# ${r.app} — ${r.verdict}`, '', `Run: ${r.startedAt}`, ''];
  if (cfg.note) lines.push(`> ${cfg.note}`, '');
  lines.push('## Stages', '', '| # | Stage | Status | Detail |', '|---|---|---|---|');
  r.stages.forEach((s) => lines.push(`| ${s.stage} | ${s.name} | ${s.status.toUpperCase()} | ${cell(s.detail)} |`));
  if (r.checks.length) {
    lines.push('', '## Playwright checks', '', '| # | Check | Status | Detail |', '|---|---|---|---|');
    r.checks.forEach((c) => lines.push(`| ${c.id} | ${c.name} | ${c.status.toUpperCase()} | ${cell(c.detail)} |`));
    lines.push('', 'Screenshots: `screenshot-moved.png` (Move on, controls dragged, drawer closed), `screenshot-drawer.png` (Port drawer open).');
  }
  if (r.video) {
    lines.push('', '## Demo video', '', r.video.files.map((f) => `[\`${f}\`](${f})`).join(' · '), '', ...r.video.steps.map((t, i) => `${i + 1}. ${t}`));
  }
  for (const [title, list] of [['Uncaught page errors (all, including the app\'s own)', r.pageErrors], ['Console errors only seen with the Port', r.consoleErrors]]) {
    if (list && list.length) lines.push('', `## ${title}`, '', ...list.slice(0, 15).map((e) => '- `' + cell(e).slice(0, 300) + '`'));
  }
  return lines.join('\n') + '\n';
}

function summary() {
  const rows = numbered().map((id) => readJson(path.join(RESULTS, id, 'result.json'))).filter(Boolean);
  const lines = ['# ATTa results', '',
    '| # | App | Verdict | Intake | Build | Serve | Proxy | Playwright | Video | Deploy bundle | Checks | Run |',
    '|---|---|---|---|---|---|---|---|---|---|---|---|'];
  const dep = (id) => { const d = readJson(path.join(RESULTS, id, 'deploy.json')); return d ? `[${d.status.toUpperCase()}](${id}/DEPLOY.md)` : '—'; };
  for (const r of rows) {
    const st = (n) => (r.stages.find((s) => s.stage === n) || { status: '—' }).status.toUpperCase();
    const checks = r.checks.map((c) => `${c.id}:${c.status[0].toUpperCase()}`).join(' ') || '—';
    lines.push(`| ${r.number} | [${r.app}](${r.app}/REPORT.md) | **${r.verdict}** | ${st(0)} | ${st(1)} | ${st(2)} | ${st(3)} | ${st(4)} | ${r.video ? `[mp4](${r.app}/${r.video.files[0]})` : st(5)} | ${dep(r.app)} | ${checks} | ${r.startedAt.slice(0, 16).replace('T', ' ')} |`);
  }
  lines.push('', 'Checks: C1 page loads · C2 hook tag before </head> · C3 port.js live · C4 mover attached in drawer slot · ' +
    'C5 app controls found · C6 drag a link · C7 drag a button · C8 move survives reload · C9 rename · C10 reset restores · ' +
    'C11 drawer opens · C12 no new errors with the Port · C13 app CSP lets the Port run · C14 redirects and links stay on the proxy · C15 customer skins via the proxy · C16 sticky-notes capability.',
  'P=pass F=fail W=warn S=skip.');
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
    if (!ids.length) throw new Error('no apps to run — add one with: node atta.js add <zip|dir|git-url>');
    let failed = 0;
    for (const id of ids) if ((await runOne(id)).verdict === 'FAIL') failed++;
    console.log('\n' + summary());
    process.exitCode = failed ? 1 : 0;
  } else if (cmd === 'package') {
    const all = numbered();
    const ids = args.length ? all.filter((id) => args.some((a) => id === a || id.startsWith(a.padStart(3, '0') + '-'))) : all;
    for (const id of ids) await packageOne(id);
    console.log('\n' + summary());
  } else if (cmd === 'results') {
    console.log(summary());
  } else {
    console.log('usage: node atta.js add <zip|dir|git-url> [name] | run [NNN ...] | results');
  }
}

main().catch((e) => { console.error(e.message); process.exit(1); });
