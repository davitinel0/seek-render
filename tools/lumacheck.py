"""python tools/lumacheck.py <video.mp4> [step_s=3] — QA on the ENCODED file (previews can hide bugs that only build up
over a long render, like the light veil that accumulated). Samples a frame every step seconds and flags any whose
mean brightness falls under 22/255 (near black) outside intentional black moments."""
import subprocess, sys
import numpy as np

video = sys.argv[1]; step = float(sys.argv[2]) if len(sys.argv) > 2 else 3.0
dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", video], capture_output=True, text=True).stdout)
w, h = 96, 54
raw = subprocess.run(["ffmpeg", "-v", "error", "-i", video, "-vf", f"fps=1/{step},scale={w}:{h},format=gray", "-f", "rawvideo", "-"], capture_output=True).stdout
frames = np.frombuffer(raw, np.uint8).reshape(-1, h, w)
means = frames.reshape(len(frames), -1).mean(1)
dark = [(i * step, m) for i, m in enumerate(means) if m < 22]
print(f"{len(frames)} samples, mean brightness {means.mean():.0f}/255, darkest {means.min():.0f} at {means.argmin() * step:.0f}s")
for t, m in dark: print(f"  DARK {int(t // 60)}:{t % 60:04.1f}  {m:.0f}/255")
print("PASS" if not dark else f"FAIL ({len(dark)} dark samples)")
sys.exit(1 if dark else 0)
