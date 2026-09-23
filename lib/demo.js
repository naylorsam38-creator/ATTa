'use strict';
// Records a demo video of one app running through the proxy, driven through the
// Port's own UI: attach the mover by typing its path, move and rename controls,
// pin a sticky note, then reload as each customer to show their skin.
// Output: <outDir>/demo.webm (+ demo.mp4 when an ffmpeg binary is available).
const fs = require('fs');
const path = require('path');
const { execFileSync } = require('child_process');
const { chromium } = require('playwright');
const { pickTarget, PICKABLE } = require('./check');

const W = 1280, H = 800;

function ffmpegBinary() {
  if (process.env.FFMPEG) return process.env.FFMPEG;
  try {
    return execFileSync('python3', ['-c', 'import imageio_ffmpeg;print(imageio_ffmpeg.get_ffmpeg_exe())']).toString().trim();
  } catch (_) { return null; }
}

// Caption bar + visible cursor, styled through CSSOM so app CSPs don't block them. Test harness only.
async function overlay(page, text) {
  await page.evaluate((text) => {
    const css = (el, p) => { for (const [k, v] of Object.entries(p)) el.style.setProperty(k, v, 'important'); return el; };
    let cap = document.getElementById('atta-caption');
    if (!cap) {
      cap = css(document.createElement('div'), { position: 'fixed', left: '50%', top: '10px', transform: 'translateX(-50%)',
        'z-index': '2147483647', background: 'rgba(20,20,20,.88)', color: '#fff', font: '600 15px/1.3 system-ui, sans-serif',
        padding: '8px 14px', 'border-radius': '8px', 'pointer-events': 'none', 'max-width': '80vw', 'text-align': 'center', filter: 'none' });
      cap.id = 'atta-caption';
      document.documentElement.append(cap);
      const dot = css(document.createElement('div'), { position: 'fixed', left: '0', top: '0', width: '18px', height: '18px',
        'margin-left': '-9px', 'margin-top': '-9px', 'border-radius': '50%', background: 'rgba(255,60,60,.75)',
        border: '2px solid #fff', 'z-index': '2147483647', 'pointer-events': 'none', transition: 'transform .05s' });
      dot.id = 'atta-cursor';
      document.documentElement.append(dot);
      addEventListener('pointermove', (e) => { dot.style.setProperty('left', e.clientX + 'px', 'important'); dot.style.setProperty('top', e.clientY + 'px', 'important'); }, true);
    }
    cap.textContent = text;
  }, text).catch(() => {});
}

async function glide(page, from, to, steps = 25) {
  await page.mouse.move(from.x, from.y);
  await page.mouse.move(to.x, to.y, { steps });
}

async function recordDemo({ proxyUrl, appId, pagePath = '/', skins = {}, outDir }) {
  const tmp = path.join(outDir, '.video');
  fs.rmSync(tmp, { recursive: true, force: true });
  const browser = await chromium.launch();
  const steps = [];
  let video;
  try {
    const context = await browser.newContext({ viewport: { width: W, height: H }, recordVideo: { dir: tmp, size: { width: W, height: H } } });
    const page = await context.newPage();
    video = page.video();
    let answer = null;
    page.on('dialog', (d) => (d.type() === 'prompt' && answer ? d.accept(answer) : d.dismiss()).catch(() => {}));
    const pause = (ms) => page.waitForTimeout(ms);
    const shadow = (sel) => page.locator(`#capability-port ${sel}`);
    const step = async (text, ms = 1800) => { steps.push(text); await overlay(page, `${appId} · ${text}`); await pause(ms); };
    const load = async () => {
      await page.goto(proxyUrl + pagePath, { waitUntil: 'load', timeout: 45000 }).catch(() => {});
      await page.waitForLoadState('networkidle', { timeout: 6000 }).catch(() => {});
      await page.waitForFunction(() => window.__capabilityPort, null, { timeout: 15000 }).catch(() => {});
    };

    await load();
    await step('the app, served through ui-bridge/proxy.js', 2500);

    // Attach the mover through the Port's own UI, the way a person would.
    await shadow('.fab').click();
    await step('Capability Port (injected before </head>) — attach the button mover');
    await shadow('textarea').fill('/_cp/caps/mover.mjs');
    await pause(600);
    await shadow('.go').click();
    await page.waitForFunction(() => document.getElementById('capability-port').shadowRoot.querySelector('section[data-capability="button-mover"]'), null, { timeout: 15000 });
    await step('button-mover attached in the drawer slot');
    await shadow('section[data-capability="button-mover"] [data-t]').click();
    await step('Move: on — every button and link is now draggable', 1500);
    await page.evaluate(() => document.getElementById('capability-port').shadowRoot.querySelector('.panel').classList.remove('open'));

    // Drag a button (then a link), slowly enough to watch.
    for (const kind of ['button', 'link']) {
      const t = await page.evaluate(pickTarget, [PICKABLE, kind]);
      if (t.x === undefined) continue;
      await glide(page, { x: t.x - 150, y: t.y + 120 }, t);
      await page.mouse.down();
      await glide(page, t, { x: t.x + t.dx, y: t.y + t.dy }, 30);
      await page.mouse.up();
      await step(`${kind} "${t.label.slice(0, 24)}" moved`, 1500);
    }

    // Rename by double-click.
    const r = await page.evaluate(pickTarget, [PICKABLE, 'any']);
    if (r.x !== undefined) {
      answer = 'Renamed by ATTa';
      await glide(page, { x: r.x + 80, y: r.y + 80 }, r, 15);
      await page.mouse.dblclick(r.x, r.y);
      answer = null;
      await step(`"${r.label.slice(0, 24)}" renamed to "Renamed by ATTa"`, 2000);
    }
    await shadow('.fab').click();
    await shadow('section[data-capability="button-mover"] [data-t]').click();

    // Sticky notes: a second capability, also attached through the Port UI.
    await shadow('textarea').first().fill('/_cp/caps/sticky-notes.mjs');
    await pause(500);
    await shadow('.go').click();
    await page.waitForFunction(() => document.getElementById('capability-port').shadowRoot.querySelector('section[data-capability="sticky-notes"]'), null, { timeout: 15000 });
    await shadow('section[data-capability="sticky-notes"] textarea').fill(`Sticky note on ${appId} — pinned through the proxy`);
    await step('sticky-notes capability attached — pin a note');
    await shadow('section[data-capability="sticky-notes"] [data-pin]').click();
    await page.evaluate(() => document.getElementById('capability-port').shadowRoot.querySelector('.panel').classList.remove('open'));
    const note = await page.locator('[data-sticky-note]').first().boundingBox().catch(() => null);
    if (note) {
      const from = { x: note.x + 60, y: note.y + 20 };
      await glide(page, { x: from.x + 200, y: from.y + 200 }, from, 15);
      await page.mouse.down();
      await glide(page, from, { x: from.x + 260, y: from.y + 220 }, 30);
      await page.mouse.up();
    }
    await step('note pinned on the live app and dragged into place', 2000);

    // Skins: the proxy picks the stylesheet from the customer header. Moves and notes persist.
    for (const [customer, skin] of Object.entries(skins)) {
      await page.setExtraHTTPHeaders({ 'x-authenticated-customer-id': customer });
      await load();
      // The Port restores both capabilities from storage (which opens the drawer); close it to show the app.
      await page.waitForFunction(() => window.__capabilityPort.attached().length >= 2, null, { timeout: 15000 }).catch(() => {});
      await pause(1200);
      await page.evaluate(() => document.getElementById('capability-port').shadowRoot.querySelector('.panel').classList.remove('open')).catch(() => {});
      await step(`customer "${customer}" → skin "${skin}" (moves, names and notes kept)`, 2800);
    }
    await page.setExtraHTTPHeaders({});
    await load();
    await step('no customer header → original look', 2200);

    // Put everything back.
    await page.evaluate(() => document.getElementById('capability-port').shadowRoot.querySelector('.panel').classList.add('open'));
    await pause(400);
    await shadow('section[data-capability="button-mover"] [data-r]').click().catch(() => {});
    await shadow('section[data-capability="sticky-notes"] [data-clear]').click().catch(() => {});
    await step('Reset + Clear — app back exactly as it was', 2200);
    await context.close();
  } finally {
    await browser.close();
  }

  const webm = path.join(outDir, 'demo.webm');
  fs.renameSync(await video.path(), webm);
  fs.rmSync(tmp, { recursive: true, force: true });
  let mp4 = null;
  const ff = ffmpegBinary();
  if (ff) {
    try {
      mp4 = path.join(outDir, 'demo.mp4');
      execFileSync(ff, ['-y', '-loglevel', 'error', '-i', webm, '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-preset', 'veryfast', '-crf', '28', '-movflags', '+faststart', mp4]);
    } catch (_) { mp4 = null; }
  }
  return { webm, mp4, steps };
}

module.exports = { recordDemo };
