// node render.mjs <page.html> <out.mp4> [--fps 60] [--sub 4] [--from 0] [--to end] [--vert] [--map edit.json] [--software]
// The page draws into <canvas id="c"> and exposes window.seek(t) (a pure function of t) and window.DURATION (seconds).
// --vert: native 9:16 (1080x1920). --map {segs: [[t0, t1], ...], hold: s}: render an edit of the film (a reel): output
// time runs through the segments in order (hard cut between them), then holds the last frame `hold` s with window.REEL_END.
// Seeks every frame (window.seek(t), pure), averages N subframes for motion blur, encodes the canvas
// in-page with WebCodecs H.264 (Annex B), streams chunks to Node, then ffmpeg wraps them into MP4 (no re-encode).
import { chromium } from 'playwright';
import { pathToFileURL } from 'url';
import fs from 'fs'; import { execFileSync } from 'child_process';

const args = process.argv.slice(2), opt = k => { const i = args.indexOf('--' + k); return i >= 0 ? args[i + 1] : null; };
const [html, out] = args;
const FPS = +(opt('fps') || 60), SUB = +(opt('sub') || 4);
const h264 = out.replace(/\.mp4$/, '.h264'); const fd = fs.openSync(h264, 'w');

// GPU path: system Chrome + ANGLE + hardware H.264 (--software: bundled Chromium / OpenH264)
const HW = !args.includes('--software');
const ANGLE = opt('angle') || (process.platform === 'win32' ? 'd3d11' : 'default');
const b = await chromium.launch(HW ? { channel: 'chrome', args: ['--use-angle=' + ANGLE, '--enable-gpu-rasterization', '--ignore-gpu-blocklist', '--allow-file-access-from-files', '--autoplay-policy=no-user-gesture-required'] } : { args: ['--allow-file-access-from-files', '--autoplay-policy=no-user-gesture-required'] }); /* file access: real footage (<video>) drawn into the canvas stays untainted */
const VERT = args.includes('--vert'), FW = VERT ? 1080 : 1920, FH = VERT ? 1920 : 1080, MAP = opt('map') ? JSON.parse(fs.readFileSync(opt('map'), 'utf8')) : null;
const p = await b.newPage({ viewport: { width: FW, height: FH } });
p.on('pageerror', e => console.error('PAGEERR', e.message));
p.on('console', m => console.log('[page]', m.text()));
await p.exposeFunction('writeChunk', b64 => { fs.writeSync(fd, Buffer.from(b64, 'base64')); });
await p.goto(pathToFileURL(html).href + (VERT ? '?vert=1&t=0' : '?t=0'));

const t0 = Date.now();
const n = await p.evaluate(async ({ FPS, SUB, from, to, HW, FW, FH, MAP }) => {
  if (window.ready) await window.ready;
  seek(0);
  const segs = MAP ? MAP.segs : null, segT = []; let acc0 = 0; if (segs) for (const [a, b] of segs) { segT.push(acc0); acc0 += b - a; }
  const mapT = r => { if (!segs) return { t: r, cut: false }; for (let i = segs.length - 1; i >= 0; i--) if (r >= segT[i] || i === 0) { const t = segs[i][0] + r - segT[i]; if (i === segs.length - 1 && t > segs[i][1]) { window.REEL_END = t - segs[i][1]; return { t: segs[i][1] - 1e-3, i, hold: true }; } window.REEL_END = null; return { t: Math.min(t, segs[i][1] - 1e-4), i }; } };
  const total = segs ? acc0 + (MAP.hold || 0) : (window.DURATION || (window.FILM && FILM.timings.total)), start = Math.round((from || 0) * FPS), end = Math.round((to || total) * FPS);
  const src = document.getElementById('c'), acc = document.createElement('canvas'); acc.width = FW; acc.height = FH;
  const a = acc.getContext('2d', { alpha: false });
  // GOP-safe encoding: chunks of the current 2 s GOP are held until it flushes cleanly; if the (hardware) encoder
  // errors, it is recreated and only that GOP is redone (second failure in a row: software encoder for that GOP)
  let pending = [], gop = [], err = null, fails = 0, encHW = null, enc = null;
  const flush = async () => { if (!pending.length) return; const all = new Blob(pending); pending = [];
    const buf = new Uint8Array(await all.arrayBuffer()); let s = ''; for (let i = 0; i < buf.length; i += 0x8000) s += String.fromCharCode.apply(null, buf.subarray(i, i + 0x8000));
    await window.writeChunk(btoa(s)); };
  const mk = hw => { if (enc && encHW === hw && !err) return; try { enc && enc.close(); } catch (e) {} err = null; encHW = hw;
    enc = new VideoEncoder({ output: c => { const d = new Uint8Array(c.byteLength); c.copyTo(d); gop.push(d); }, error: e => { err = e; } });
    enc.configure({ codec: 'avc1.64002a', width: FW, height: FH, framerate: FPS, bitrate: 16e6, bitrateMode: 'variable', latencyMode: 'quality', hardwareAcceleration: hw ? 'prefer-hardware' : 'no-preference', avc: { format: 'annexb' } }); };
  const closeGop = async () => { if (!err) { try { await enc.flush(); } catch (e) { err = err || e; } }
    if (err) { console.log(`encoder error (${err.message}); redoing GOP from frame ${gopStart}`); gop = []; fails++; mk(HW && fails < 2); return false; }
    pending.push(...gop); gop = []; fails = 0; if (pending.length > 120) await flush(); return true; };
  mk(HW); let gopStart = start;
  for (let f = start; f < end; f++) {
    if (f > gopStart && (f - start) % (FPS * 2) === 0) { if (!(await closeGop())) { f = gopStart - 1; continue; } gopStart = f; mk(HW); }
    // motion blur: running average of SUB subframes spread across the frame interval (shutter 360°)
    // adaptive: 1 sample right after a hard cut (never blend two shots), SUB normally, up to 3x SUB on fast camera moves
    const m0 = mapT(f / FPS), m1 = mapT((f + 1) / FPS - 1e-6), segCut = segs && (m0.i !== m1.i || m0.hold || f === 0 || mapT((f - 1) / FPS).i !== m0.i);
    const f0 = m0.t, f1 = m0.t + 1 / FPS, cut = segCut ? null : window.cutIn ? window.cutIn(f0, f1 - 1e-6) : null;
    const sp = (!cut && window.camSpeed) ? window.camSpeed(f0, 1 / FPS) : 0, n = segCut ? 1 : cut ? 1 : (window.isStage && window.isStage(f0)) ? Math.max(2, SUB / 2) : Math.min(SUB * 3, Math.max(SUB, Math.ceil(sp / 5)));
    for (let k = 0; k < n; k++) {
      const tt = segs ? (mapT(f / FPS), cut ? cut + 1e-4 : segCut ? f0 : f0 + k / n / FPS) : cut ? cut + 1e-4 : (f + k / n) / FPS;
      if (window.prepareFrame) await window.prepareFrame(tt); /* footage: seek the <video> elements to their exact frame first */
      seek(tt);
      a.globalAlpha = 1 / (k + 1); a.drawImage(src, 0, 0);
    }
    a.globalAlpha = 1;
    if (!err) { const vf = new VideoFrame(acc, { timestamp: Math.round((f - start) * 1e6 / FPS), duration: Math.round(1e6 / FPS) });
      try { enc.encode(vf, { keyFrame: f === gopStart }); } catch (e) { err = err || e; } vf.close(); }
    while (!err && enc.encodeQueueSize > 6) await new Promise(r => setTimeout(r, 1));
    if (f % (FPS * 10) === 0) console.log(`frame ${f}/${end}`);
    if (f === end - 1 && !(await closeGop())) f = gopStart - 1;
  }
  await flush(); try { enc.close(); } catch (e) {}
  return end - start;
}, { FPS, SUB, from: +(opt('from') || 0), to: opt('to') ? +opt('to') : null, HW, FW, FH, MAP });
fs.closeSync(fd); await b.close();
console.log(`${n} frames in ${((Date.now() - t0) / 1000).toFixed(0)}s`);
execFileSync('ffmpeg', ['-y', '-v', 'error', '-r', String(FPS), '-f', 'h264', '-i', h264, '-c', 'copy', '-movflags', '+faststart', out]);
fs.unlinkSync(h264); console.log('wrote', out);
