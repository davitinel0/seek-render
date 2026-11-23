// tools/strip.mjs — filmstrip of a seekable page: frames from t0 to t1 at a fixed rate, tiled into one JPEG.
// A single still cannot show motion; a strip can. If frames of the same shot look identical, that shot is not moving.
// It also shows cuts and transitions at a glance. Each tile is stamped with its time; read left to right, top to bottom.
//
// Usage: node tools/strip.mjs <page.html> <out.jpg> <t0> <t1> [--fps 2] [--cols 8] [--width 480] [--vert] [--software]
//
// Same page contract as render.mjs: <canvas id="c">, window.seek(t), optional window.ready and window.prepareFrame(t).
// --vert loads the page with ?vert=1 at 1080x1920 (tiles become portrait). --software uses the bundled Chromium instead
// of the installed Google Chrome (the default, as in render.mjs).
import { chromium } from 'playwright';
import { pathToFileURL } from 'url';
import fs from 'fs';

const args = process.argv.slice(2);
const opt = (k, d) => { const i = args.indexOf('--' + k); return i >= 0 ? args[i + 1] : d; };
const [html, out, t0s, t1s] = args;  // positional arguments first, options after
if (!html || !out || t0s === undefined || t1s === undefined) {
  console.error('usage: node tools/strip.mjs <page.html> <out.jpg> <t0> <t1> [--fps 2] [--cols 8] [--width 480] [--vert] [--software]');
  process.exit(2);
}
const t0 = +t0s, t1 = +t1s, fps = +opt('fps', 2), cols = +opt('cols', 8);
const VERT = args.includes('--vert'), FW = VERT ? 1080 : 1920, FH = VERT ? 1920 : 1080;
const TW = +opt('width', VERT ? 270 : 480), TH = Math.round(TW * FH / FW);

const launchArgs = ['--allow-file-access-from-files', '--autoplay-policy=no-user-gesture-required'];
const b = await chromium.launch(args.includes('--software') ? { args: launchArgs } : { channel: 'chrome', args: launchArgs });
const p = await b.newPage({ viewport: { width: FW, height: FH } });
const errs = []; p.on('pageerror', e => errs.push(e.message));
await p.goto(pathToFileURL(html).href + (VERT ? '?vert=1&t=0' : '?t=0'));
await p.evaluate(async () => { if (window.ready) await window.ready; });

const ts = []; for (let t = t0; t <= t1 + 1e-6; t += 1 / fps) ts.push(+t.toFixed(3));
const tiles = [];
for (const t of ts) tiles.push(await p.evaluate(async ([t, TW, TH]) => {
  if (window.prepareFrame) await window.prepareFrame(t);
  window.seek(t);
  const c = document.getElementById('c'), s = document.createElement('canvas'); s.width = TW; s.height = TH;
  const g = s.getContext('2d'); g.drawImage(c, 0, 0, TW, TH);
  g.fillStyle = 'rgba(0,0,0,0.6)'; g.fillRect(0, 0, 74, 20); g.fillStyle = '#ff0'; g.font = '14px sans-serif'; g.fillText(t.toFixed(2), 5, 15);
  return s.toDataURL('image/jpeg', 0.8);
}, [t, TW, TH]));

const rows = Math.ceil(tiles.length / cols);
const sheet = await p.evaluate(async ([tiles, cols, rows, TW, TH]) => {
  const s = document.createElement('canvas'); s.width = Math.min(cols, tiles.length) * TW; s.height = rows * TH;
  const g = s.getContext('2d'); g.fillStyle = '#222'; g.fillRect(0, 0, s.width, s.height);
  for (let k = 0; k < tiles.length; k++) {
    const im = new Image(); await new Promise(r => { im.onload = r; im.src = tiles[k]; });
    g.drawImage(im, (k % cols) * TW, Math.floor(k / cols) * TH);
  }
  return s.toDataURL('image/jpeg', 0.82);
}, [tiles, cols, rows, TW, TH]);
fs.writeFileSync(out, Buffer.from(sheet.split(',')[1], 'base64'));
console.log(`${out}: ${tiles.length} frames (${t0}-${t1} s at ${fps} fps)`, errs.length ? ['page errors:', ...errs.slice(0, 3)] : 'no page errors');
await b.close();
