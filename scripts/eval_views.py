"""Check a reconstruction: render every original camera view and compare it with the photo.

Low scores point at photos whose camera pose is wrong (or areas seen by too few photos).
Writes output/<scene>/check.png (render | photo, per view) and prints a PSNR per view.

Usage: python scripts/eval_views.py <scene> [--size 240]
"""

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
FFMPEG = next((ROOT / "tools" / "ffmpeg").glob("*/bin/ffmpeg.exe"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("scene")
    ap.add_argument("--size", type=int, default=240, help="width of each tile")
    a = ap.parse_args()

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from render_path import read_cameras
    cams = read_cameras(ROOT / "output" / a.scene, a.scene)
    dataset = Path((ROOT / "reconstruction" / a.scene / "dataset.txt").read_text().strip())
    first = Image.open(dataset / "images" / cams[0]["name"])
    W = a.size
    H = round(W * first.height / first.width / 2) * 2
    W -= W % 2

    with tempfile.TemporaryDirectory() as td:
        vid = Path(td) / "views.mp4"
        subprocess.run([sys.executable, str(ROOT / "scripts" / "render_path.py"), a.scene, "--shot", "views",
                        "--width", str(W), "--height", str(H), "--out", str(vid)], check=True)
        subprocess.run([str(FFMPEG), "-v", "error", "-i", str(vid), str(Path(td) / "f_%03d.png")], check=True)
        renders = sorted(Path(td).glob("f_*.png"))
        rows, scores = [], []
        for i, c in enumerate(cams):
            if i >= len(renders):
                break
            r = np.asarray(Image.open(renders[i]).convert("RGB").resize((W, H)), dtype=np.float32)
            p = np.asarray(Image.open(dataset / "images" / c["name"]).convert("RGB").resize((W, H)),
                           dtype=np.float32)
            mse = np.mean((r - p) ** 2)
            psnr = 10 * np.log10(255 ** 2 / max(mse, 1e-6))
            scores.append(psnr)
            rows.append((r.astype(np.uint8), p.astype(np.uint8), psnr, c["name"]))

    cols = 4
    n = len(rows)
    sheet = Image.new("RGB", (cols * 2 * W, ((n + cols - 1) // cols) * H), "black")
    d = ImageDraw.Draw(sheet)
    for k, (r, p, s, name) in enumerate(rows):
        x, y = (k % cols) * 2 * W, (k // cols) * H
        sheet.paste(Image.fromarray(r), (x, y))
        sheet.paste(Image.fromarray(p), (x + W, y))
        d.rectangle([x, y, x + 120, y + 16], fill="black")
        d.text((x + 3, y + 2), f"#{k} {s:.1f} dB", fill="red" if s < 15 else "lime")
        print(f"view {k:3d}  {s:5.1f} dB  {name}")
    out = ROOT / "output" / a.scene / "check.png"
    sheet.save(out)
    print(f"mean {np.mean(scores):.1f} dB  ->  {out}   (left = render, right = photo; < 15 dB = suspect)")


if __name__ == "__main__":
    main()
