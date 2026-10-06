"""Quick CPU preview of a Gaussian-splat .ply (approximate: splats drawn as soft discs).

Renders the scene from camera poses in a COLMAP images.txt (or a default view) into a PNG
contact sheet - handy to sanity-check a reconstruction without opening the browser.

Usage: python scripts/preview_ply.py <scene.ply> [--poses scene_images.txt] [--out preview.png]
"""

import argparse
from pathlib import Path

import numpy as np
from PIL import Image

SH_C0 = 0.28209479177387814


def load_ply(path):
    with open(path, "rb") as f:
        header = b""
        while not header.endswith(b"end_header\n"):
            header += f.readline()
        lines = header.decode("ascii", "replace").splitlines()
        n = next(int(l.split()[2]) for l in lines if l.startswith("element vertex"))
        props, in_vertex = [], False
        for l in lines:
            if l.startswith("element"):
                in_vertex = l.startswith("element vertex")
            elif l.startswith("property") and in_vertex:
                _, typ, name = l.split()
                props.append((name, {"float": "<f4", "double": "<f8", "uchar": "u1",
                                     "int": "<i4", "uint": "<u4"}[typ]))
        data = np.frombuffer(f.read(n * np.dtype(props).itemsize), dtype=props, count=n)
    xyz = np.stack([data["x"], data["y"], data["z"]], 1).astype(np.float64)
    rgb = np.clip(0.5 + SH_C0 * np.stack([data[f"f_dc_{i}"] for i in range(3)], 1), 0, 1)
    alpha = 1 / (1 + np.exp(-data["opacity"].astype(np.float64)))
    scale = np.exp(np.max(np.stack([data[f"scale_{i}"] for i in range(3)], 1), 1).astype(np.float64))
    return xyz, rgb, alpha, scale


def read_poses(path, limit):
    rows = [l.split() for l in Path(path).read_text().splitlines() if l and not l.startswith("#")]
    poses = [r for r in rows if len(r) == 10]
    if len(poses) > limit:
        poses = [poses[i] for i in np.linspace(0, len(poses) - 1, limit).astype(int)]
    out = []
    for r in poses:
        qw, qx, qy, qz, tx, ty, tz = map(float, r[1:8])
        R = np.array([
            [1 - 2 * (qy * qy + qz * qz), 2 * (qx * qy - qz * qw), 2 * (qx * qz + qy * qw)],
            [2 * (qx * qy + qz * qw), 1 - 2 * (qx * qx + qz * qz), 2 * (qy * qz - qx * qw)],
            [2 * (qx * qz - qy * qw), 2 * (qy * qz + qx * qw), 1 - 2 * (qx * qx + qy * qy)]])
        out.append((R, np.array([tx, ty, tz]), r[9]))
    return out


def render(xyz, rgb, alpha, scale, R, t, W, H, f):
    cam = xyz @ R.T + t
    z = cam[:, 2]
    m = z > 0.05
    u = f * cam[m, 0] / z[m] + W / 2
    v = f * cam[m, 1] / z[m] + H / 2
    r = np.clip(f * 2 * scale[m] / z[m], 0.6, 6)
    keep = (u > -r) & (u < W + r) & (v > -r) & (v < H + r)
    u, v, r, zz = u[keep], v[keep], r[keep], z[m][keep]
    c, a = rgb[m][keep], alpha[m][keep]
    img = np.zeros((H, W, 3))
    trans = np.ones((H, W))
    order = np.argsort(zz)  # front to back compositing
    for i in order:
        x0, y0, rr = u[i], v[i], r[i]
        xi0, xi1 = max(int(x0 - rr), 0), min(int(x0 + rr) + 1, W)
        yi0, yi1 = max(int(y0 - rr), 0), min(int(y0 + rr) + 1, H)
        if xi0 >= xi1 or yi0 >= yi1:
            continue
        T = trans[yi0:yi1, xi0:xi1]
        aa = a[i] * T
        img[yi0:yi1, xi0:xi1] += aa[..., None] * c[i]
        trans[yi0:yi1, xi0:xi1] = T * (1 - a[i])
    return (np.clip(img, 0, 1) * 255).astype(np.uint8)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ply")
    ap.add_argument("--poses")
    ap.add_argument("--out")
    ap.add_argument("--views", type=int, default=6)
    ap.add_argument("--width", type=int, default=320)
    ap.add_argument("--max-splats", type=int, default=150_000)
    ap.add_argument("--offset", type=float, nargs=3, default=(0, 0, 0),
                    help="move the camera by this (camera-space x y z) to test novel views")
    a = ap.parse_args()

    xyz, rgb, alpha, scale = load_ply(a.ply)
    print(f"{len(xyz):,} splats")
    if len(xyz) > a.max_splats:  # keep the most opaque / largest splats
        idx = np.argsort(-(alpha * scale))[: a.max_splats]
        xyz, rgb, alpha, scale = xyz[idx], rgb[idx], alpha[idx], scale[idx]
    W = a.width
    H = int(W * 4 / 3)
    poses = read_poses(a.poses, a.views) if a.poses else [(np.eye(3), np.zeros(3), "origin")]
    tiles = []
    for R, t, name in poses:
        t = t - np.array(a.offset)  # camera moves by +offset in its own frame
        tiles.append(render(xyz, rgb, alpha, scale, R, t, W, H, f=W * 0.9))
    cols = min(len(tiles), 3)
    rows = (len(tiles) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * W, rows * H))
    for k, tile in enumerate(tiles):
        sheet.paste(Image.fromarray(tile), ((k % cols) * W, (k // cols) * H))
    out = a.out or str(Path(a.ply).with_suffix(".preview.png"))
    sheet.save(out)
    print("->", out)


if __name__ == "__main__":
    main()
