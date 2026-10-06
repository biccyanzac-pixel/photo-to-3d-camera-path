"""Camera poses + initial point cloud with Depth Anything 3 (no feature matching needed).

Used by pipeline.py when --poses da3 (default when COLMAP fails). Runs inside tools/da3-env.
Classic SfM (COLMAP) needs many overlapping views; DA3 is a feed-forward network that
predicts poses/intrinsics/depth for all images jointly, so it also works for a few
wide-baseline photos or short/blurry phone video.

Writes a COLMAP *text* model + matching full-resolution images:
    <out>/images/<name>.jpg
    <out>/sparse/0/{cameras,images,points3D}.txt

Usage: python da3_poses.py <images_dir> <out_dir> [--model depth-anything/DA3-BASE]
                                                 [--res 504] [--points 300000]
"""

import argparse
import sys
import time
from pathlib import Path

import numpy as np
import torch
from PIL import Image, ImageOps

PATCH = 14


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] [da3] {msg}", flush=True)


def processed_geometry(w0, h0, res):
    """Replicates DA3 'upper_bound_resize': longest side -> res, then round to multiple of 14."""
    s = res / max(w0, h0)
    w, h = round(w0 * s), round(h0 * s)
    return max(PATCH, round(w / PATCH) * PATCH), max(PATCH, round(h / PATCH) * PATCH)


def rotmat_to_quat(R):
    """3x3 rotation -> (qw, qx, qy, qz), COLMAP convention."""
    m = R
    t = np.trace(m)
    if t > 0:
        s = np.sqrt(t + 1.0) * 2
        return np.array([0.25 * s, (m[2, 1] - m[1, 2]) / s, (m[0, 2] - m[2, 0]) / s, (m[1, 0] - m[0, 1]) / s])
    i = int(np.argmax(np.diag(m)))
    if i == 0:
        s = np.sqrt(1.0 + m[0, 0] - m[1, 1] - m[2, 2]) * 2
        return np.array([(m[2, 1] - m[1, 2]) / s, 0.25 * s, (m[0, 1] + m[1, 0]) / s, (m[0, 2] + m[2, 0]) / s])
    if i == 1:
        s = np.sqrt(1.0 + m[1, 1] - m[0, 0] - m[2, 2]) * 2
        return np.array([(m[0, 2] - m[2, 0]) / s, (m[0, 1] + m[1, 0]) / s, 0.25 * s, (m[1, 2] + m[2, 1]) / s])
    s = np.sqrt(1.0 + m[2, 2] - m[0, 0] - m[1, 1]) * 2
    return np.array([(m[1, 0] - m[0, 1]) / s, (m[0, 2] + m[2, 0]) / s, (m[1, 2] + m[2, 1]) / s, 0.25 * s])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("images")
    ap.add_argument("out")
    ap.add_argument("--model", default="depth-anything/DA3-BASE")
    ap.add_argument("--res", type=int, default=504, help="DA3 processing resolution (longest side)")
    ap.add_argument("--points", type=int, default=300_000, help="initial points for splat training")
    ap.add_argument("--conf", type=float, default=40.0, help="drop the least confident N%% of depth")
    ap.add_argument("--device", default="auto")
    ap.add_argument("--ray-pose", action="store_true", help="use DA3's ray-based pose estimation")
    a = ap.parse_args()

    src = Path(a.images)
    out = Path(a.out)
    paths = sorted(p for p in src.rglob("*") if p.suffix.lower() in (".jpg", ".jpeg", ".png"))
    if len(paths) < 2:
        sys.exit("need at least 2 images")

    # Mixed shapes (e.g. portrait photos + landscape video): DA3 needs one shape per batch and
    # would crop everything to the overlap of all shapes. Instead centre-crop every image to
    # the aspect ratio that carries the most pixels in total (usually the photos).
    sizes = [ImageOps.exif_transpose(Image.open(p)).size for p in paths]
    by_aspect = {}
    for (w, h) in sizes:
        key = round(w / h, 2)
        by_aspect[key] = by_aspect.get(key, 0) + w * h
    target = max(by_aspect, key=by_aspect.get)
    if len(by_aspect) > 1:
        log(f"mixed image shapes {sorted(by_aspect)} -> centre-cropping all to aspect {target}")
        tmp = out / "_aspect"
        tmp.mkdir(parents=True, exist_ok=True)
        new_paths = []
        for p, (w, h) in zip(paths, sizes):
            im = ImageOps.exif_transpose(Image.open(p)).convert("RGB")
            if abs(w / h - target) > 0.01:
                if w / h > target:  # too wide
                    nw = round(h * target)
                    im = im.crop(((w - nw) // 2, 0, (w - nw) // 2 + nw, h))
                else:               # too tall
                    nh = round(w / target)
                    im = im.crop((0, (h - nh) // 2, w, (h - nh) // 2 + nh))
            q = tmp / f"{len(new_paths):05d}_{p.stem}.jpg"
            im.save(q, quality=95)
            new_paths.append(q)
        paths = new_paths

    from depth_anything_3.api import DepthAnything3

    device = a.device
    if device == "auto":
        device = "cuda" if torch.cuda.is_available() else "cpu"
    if device == "cuda":
        major = torch.cuda.get_device_capability()[0]
        if major < 7:
            # Pascal & older: bf16 is only emulated (DA3 then crashes) and fp16 is very slow
            # -> run DA3 in plain fp32 by turning its autocast into a no-op.
            import contextlib
            torch.autocast = lambda *args, **kw: contextlib.nullcontext()
            log("older GPU detected: running in fp32")
        elif major < 8:
            torch.cuda.is_bf16_supported = lambda *args, **kw: False  # use fp16 instead
    log(f"loading {a.model} on {device} ...")
    model = DepthAnything3.from_pretrained(a.model).to(device).eval()

    log(f"predicting poses + depth for {len(paths)} images at {a.res}px ...")
    t0 = time.time()
    try:
        with torch.no_grad():
            pred = model.inference([str(p) for p in paths], process_res=a.res,
                                   process_res_method="upper_bound_resize", use_ray_pose=a.ray_pose)
    except torch.OutOfMemoryError:
        if device != "cuda":
            raise
        log("GPU out of memory -> retrying on CPU (slower)")
        torch.cuda.empty_cache()
        model = model.to("cpu")
        with torch.no_grad():
            pred = model.inference([str(p) for p in paths], process_res=a.res,
                                   process_res_method="upper_bound_resize", use_ray_pose=a.ray_pose)
    log(f"inference took {time.time() - t0:.0f}s")

    depth = np.asarray(pred.depth)          # (N, h, w)
    conf = np.asarray(pred.conf)            # (N, h, w)
    K = np.asarray(pred.intrinsics, dtype=np.float64)   # (N, 3, 3) for the processed (cropped) image
    E = np.asarray(pred.extrinsics, dtype=np.float64)   # (N, 3|4, 4) world->camera, OpenCV
    imgs = np.asarray(pred.processed_images)            # (N, h, w, 3) uint8
    N, hc, wc = depth.shape

    (out / "images").mkdir(parents=True, exist_ok=True)
    sp = out / "sparse" / "0"
    sp.mkdir(parents=True, exist_ok=True)

    cams, ims = [], []
    for i, p in enumerate(paths):
        im = ImageOps.exif_transpose(Image.open(p)).convert("RGB")
        w0, h0 = im.size
        wp, hp = processed_geometry(w0, h0, a.res)
        # DA3 centre-crops mixed-size batches to the smallest (hc, wc): apply the same crop
        # at full resolution so the predicted intrinsics stay exact.
        left, top = (wp - wc) // 2, (hp - hc) // 2
        sx, sy = w0 / wp, h0 / hp
        box = (round(left * sx), round(top * sy), round((left + wc) * sx), round((top + hc) * sy))
        crop = im.crop(box)
        name = p.name if p.parent.name == "_aspect" else f"{i:05d}_{p.stem}.jpg"
        crop.save(out / "images" / name, quality=95)
        W, H = crop.size
        fx, fy = K[i, 0, 0] * W / wc, K[i, 1, 1] * H / hc
        cx, cy = K[i, 0, 2] * W / wc, K[i, 1, 2] * H / hc
        cams.append(f"{i + 1} PINHOLE {W} {H} {fx:.6f} {fy:.6f} {cx:.6f} {cy:.6f}")
        R, t = E[i, :3, :3], E[i, :3, 3]
        q = rotmat_to_quat(R)
        q /= np.linalg.norm(q)
        ims.append(f"{i + 1} {q[0]:.9f} {q[1]:.9f} {q[2]:.9f} {q[3]:.9f} "
                   f"{t[0]:.9f} {t[1]:.9f} {t[2]:.9f} {i + 1} {name}\n")

    # Back-project confident depth into a coloured world point cloud (training init).
    thr = np.percentile(conf, a.conf)
    ys, xs = np.mgrid[0:hc, 0:wc]
    pts, cols = [], []
    for i in range(N):
        m = (conf[i] >= thr) & np.isfinite(depth[i]) & (depth[i] > 0)
        z = depth[i][m]
        x = (xs[m] - K[i, 0, 2]) / K[i, 0, 0] * z
        y = (ys[m] - K[i, 1, 2]) / K[i, 1, 1] * z
        pc = np.stack([x, y, z], 1)
        R, t = E[i, :3, :3], E[i, :3, 3]
        pts.append((pc - t) @ R)          # camera -> world:  R^T (p - t)
        cols.append(imgs[i][m])
    pts = np.concatenate(pts)
    cols = np.concatenate(cols)
    if len(pts) > a.points:
        sel = np.random.default_rng(0).choice(len(pts), a.points, replace=False)
        pts, cols = pts[sel], cols[sel]

    (sp / "cameras.txt").write_text("# CAMERA_ID MODEL WIDTH HEIGHT PARAMS[]\n" + "\n".join(cams) + "\n")
    with open(sp / "images.txt", "w") as f:
        f.write(f"# IMAGE_ID QW QX QY QZ TX TY TZ CAMERA_ID NAME\n# Number of images: {N}\n")
        for line in ims:
            f.write(line + "\n")      # empty POINTS2D line
    with open(sp / "points3D.txt", "w") as f:
        f.write("# POINT3D_ID X Y Z R G B ERROR TRACK[]\n")
        for j, (pp, cc) in enumerate(zip(pts, cols)):
            f.write(f"{j + 1} {pp[0]:.6f} {pp[1]:.6f} {pp[2]:.6f} {cc[0]} {cc[1]} {cc[2]} 0\n")
    import shutil
    shutil.rmtree(out / "_aspect", ignore_errors=True)
    log(f"wrote {N} cameras and {len(pts):,} points -> {sp}")


if __name__ == "__main__":
    main()
