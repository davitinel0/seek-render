"""Synthesized low cinematic sound effects: deep impacts and low whooshes that still read on phone speakers.

A pure sub-bass hit (30-50 Hz) is inaudible on a phone or laptop: the speaker cannot move that low, so the "boom"
simply disappears. These sounds keep the sub for headphones and TVs, but also carry what a small speaker can play:
drum-like partials and a band-passed noise body in 120-700 Hz, a short bright attack, and tanh saturation that adds
harmonics of the sub (the ear infers the missing fundamental from them). A short dark room tail finishes each one.

Generated (mono, 48 kHz, peak -3 to -4.5 dBFS, no samples from third parties):
  opening-hit-NN   deep impact with a falling pitch, timpani-like body and a room tail (~5 s): frame 0 / title card
  hit-NN           short low impact (~2.7 s): reveals, key points, stamps
  low-whoosh-NN    low pass-by that lands on a sub thump (~2 s): screen and scene changes
Each variant uses its own seed and pitch, so repeated hits are never the identical take.

Usage:
  python audio/cinematic_hits.py out_dir [--variants 3] [--seed 0]
"""
import argparse
from pathlib import Path
import numpy as np, soundfile as sf

SR = 48000


def bp(x, lo, hi):
    """Soft band-pass in the frequency domain (linear ramps around lo and hi)."""
    X = np.fft.rfft(x); f = np.fft.rfftfreq(len(x), 1 / SR)
    m = np.clip((f - lo * 0.6) / (lo * 0.4 + 1e-9), 0, 1) * np.clip((hi * 1.4 - f) / (hi * 0.4), 0, 1)
    return np.fft.irfft(X * m, len(x))


def room(x, seed, dur=2.2, mix=0.25):
    """Simple reverb: band-passed noise with an exponential envelope, convolved (dark tail)."""
    r = np.random.default_rng(seed); n = int(SR * dur); t = np.arange(n) / SR
    ir = bp(r.standard_normal(n), 60, 1800) * np.exp(-t / (dur / 5)); ir /= np.abs(ir).sum() ** 0.5 * 8
    y = np.fft.irfft(np.fft.rfft(x, len(x) + n) * np.fft.rfft(ir, len(x) + n), len(x) + n)
    out = np.concatenate([x, np.zeros(n)]); return out + mix * y / (np.abs(y).max() + 1e-9) * np.abs(x).max()


def finish(y, peak=0.7):
    fade = int(SR * 0.15); y[-fade:] *= np.linspace(1, 0, fade); return y / np.abs(y).max() * peak


def impact(seed, f0, dur, tail):
    r = np.random.default_rng(seed); n = int(SR * dur); t = np.arange(n) / SR
    f = f0 * (1 + 0.9 * np.exp(-t / 0.06))                                                    # pitch falls into f0
    sub = 0.55 * np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t / (dur * 0.3))
    tom = np.sin(2 * np.pi * np.cumsum(f * 2.02) / SR) * np.exp(-t / 0.18) * 0.35              # timpani-like body
    skin = bp(r.standard_normal(n), 70, 700) * np.exp(-t / 0.06) * 1.1                         # the strike, muffled
    body = bp(r.standard_normal(n), 120, 420) * np.exp(-t / 0.3) * 1.2                         # the weight a phone can play
    partials = sum(a * np.sin(2 * np.pi * np.cumsum(f * k) / SR) * np.exp(-t / d)               # big-drum partials, also audible on phones
                   for k, a, d in ((3.9, 0.55, 0.16), (5.3, 0.4, 0.11), (7.1, 0.25, 0.08)))
    click = bp(r.standard_normal(n), 1500, 5000) * np.exp(-t / 0.008) * 0.35                   # defined attack
    y = (sub + tom + skin + body + partials + click) * np.minimum(1, t / 0.002)
    y = np.tanh(2.2 * y / np.abs(y).max())                                                     # saturation: harmonics of the sub
    return finish(room(y, seed + 1, tail, 0.35))


def whoosh(seed, dur):
    r = np.random.default_rng(seed); n = int(SR * dur); t = np.arange(n) / SR
    env = np.sin(np.pi * np.clip(t / dur, 0, 1)) ** 1.6
    lo = bp(r.standard_normal(n), 60, 260); mid = bp(r.standard_normal(n), 200, 700) * np.clip(t / dur, 0, 1)   # opens up a little at the end
    y = (lo + 0.8 * mid) * env
    k = int(SR * dur * 0.82); ts = np.arange(n - k) / SR
    y[k:] += (np.sin(2 * np.pi * 48 * ts) + 0.5 * np.sin(2 * np.pi * 96 * ts) + 0.3 * np.sin(2 * np.pi * 190 * ts)) \
        * np.exp(-ts / 0.2) * np.abs(y).max() * 2.0                                               # lands on a low thump
    y = np.tanh(1.8 * y / np.abs(y).max())
    return finish(room(y, seed + 2, 1.2, 0.2), 0.6)


def main():
    ap = argparse.ArgumentParser(description="Synthesize low cinematic hits and whooshes that read on phone speakers.")
    ap.add_argument("out_dir"); ap.add_argument("--variants", type=int, default=3); ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args(); out = Path(a.out_dir); out.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(a.seed); seeds = rng.integers(1, 10 ** 6, size=(3, a.variants))
    for i in range(a.variants):
        k = i + 1
        sf.write(out / f"opening-hit-{k:02d}.wav", impact(int(seeds[0, i]), float(rng.uniform(33, 41)), 2.6, 2.4), SR)
        sf.write(out / f"hit-{k:02d}.wav", impact(int(seeds[1, i]), float(rng.uniform(40, 52)), 1.3, 1.4), SR)
        sf.write(out / f"low-whoosh-{k:02d}.wav", whoosh(int(seeds[2, i]), float(rng.uniform(0.6, 0.8))), SR)
    print(f"{3 * a.variants} files -> {out} (opening-hit, hit, low-whoosh x {a.variants})")


if __name__ == "__main__":
    main()
