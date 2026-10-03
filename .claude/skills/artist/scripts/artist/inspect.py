"""Looking closer and measuring: full-resolution tiles for small defects, and four fixed measurements for what the eye gets systematically wrong (areas compared across images, lightness contrast, what touches the edge, the palette).

Every constant that moves a number is in the fingerprint, because the same image measured with a different blur or threshold gives a different number and an unlabelled number cannot be argued with later.
"""

from __future__ import annotations

import datetime
from pathlib import Path

import numpy as np

from artist import ledger, regions, sheet
from artist.errors import Failure

SHORT_SIDE = 900
BLUR_SIGMA = 1.0
INK_DELTA = 10.0
GROUND_MIN_SHARE = 0.15
ROW_MIN_INK = 0.005
EDGE_FRACTION = 0.01
ALPHA_MIN = 128
COLORS_SHORT_SIDE = 200
K = 6
SEED = 0
ITERATIONS = 20
DEFAULT_BANDS = [0.0, 25.0, 50.0, 75.0, 100.0]

COMMON = f"short={SHORT_SIDE} lanczos lab=D65 alpha<{ALPHA_MIN} excluded ground=mode(1-unit bins, blur sigma={BLUR_SIGMA}) ink_delta={INK_DELTA:g} ground_min={GROUND_MIN_SHARE}"

# sRGB (D65) to XYZ, and the D65 white point.
M = np.array([[0.4124564, 0.3575761, 0.1804375], [0.2126729, 0.7151522, 0.0721750], [0.0193339, 0.1191920, 0.9503041]])
WHITE = np.array([0.95047, 1.0, 1.08883])


# ---- colour science ---------------------------------------------------------------------------

def srgb_to_lab(rgb: np.ndarray) -> np.ndarray:
    """sRGB in 0-1 (any leading shape, last axis 3) to CIE L*a*b* under D65."""
    rgb = np.asarray(rgb, dtype=float)
    linear = np.where(rgb <= 0.04045, rgb / 12.92, ((rgb + 0.055) / 1.055) ** 2.4)
    xyz = linear @ M.T / WHITE
    delta = 6 / 29
    f = np.where(xyz > delta ** 3, np.cbrt(xyz), xyz / (3 * delta ** 2) + 4 / 29)
    return np.stack([116 * f[..., 1] - 16, 500 * (f[..., 0] - f[..., 1]), 200 * (f[..., 1] - f[..., 2])], axis=-1)


def lab_to_hex(lab: np.ndarray) -> str:
    L, a, b = lab
    fy = (L + 16) / 116
    fx, fz = fy + a / 500, fy - b / 200
    delta = 6 / 29
    xyz = np.array([fx, fy, fz])
    xyz = np.where(xyz > delta, xyz ** 3, 3 * delta ** 2 * (xyz - 4 / 29)) * WHITE
    linear = np.linalg.solve(M, xyz)
    rgb = np.where(linear <= 0.0031308, 12.92 * linear, 1.055 * np.clip(linear, 0, None) ** (1 / 2.4) - 0.055)
    r, g, bl = (int(round(float(np.clip(c, 0, 1)) * 255)) for c in rgb)
    return f"#{r:02X}{g:02X}{bl:02X}"


def delta_e2000(lab1: np.ndarray, lab2: np.ndarray) -> float:
    """CIEDE2000 between two L*a*b* colours (kL = kC = kH = 1)."""
    L1, a1, b1 = (float(x) for x in lab1)
    L2, a2, b2 = (float(x) for x in lab2)
    c1, c2 = np.hypot(a1, b1), np.hypot(a2, b2)
    c_bar = (c1 + c2) / 2
    g = 0.5 * (1 - np.sqrt(c_bar ** 7 / (c_bar ** 7 + 25 ** 7)))
    a1p, a2p = (1 + g) * a1, (1 + g) * a2
    c1p, c2p = np.hypot(a1p, b1), np.hypot(a2p, b2)
    h1p = np.degrees(np.arctan2(b1, a1p)) % 360 if c1p else 0.0
    h2p = np.degrees(np.arctan2(b2, a2p)) % 360 if c2p else 0.0
    dLp, dCp = L2 - L1, c2p - c1p
    if c1p * c2p == 0:
        dhp = 0.0
    elif abs(h2p - h1p) <= 180:
        dhp = h2p - h1p
    elif h2p - h1p > 180:
        dhp = h2p - h1p - 360
    else:
        dhp = h2p - h1p + 360
    dHp = 2 * np.sqrt(c1p * c2p) * np.sin(np.radians(dhp / 2))
    L_bar, c_bar_p = (L1 + L2) / 2, (c1p + c2p) / 2
    if c1p * c2p == 0:
        h_bar = h1p + h2p
    elif abs(h1p - h2p) <= 180:
        h_bar = (h1p + h2p) / 2
    elif h1p + h2p < 360:
        h_bar = (h1p + h2p + 360) / 2
    else:
        h_bar = (h1p + h2p - 360) / 2
    t = 1 - 0.17 * np.cos(np.radians(h_bar - 30)) + 0.24 * np.cos(np.radians(2 * h_bar)) + 0.32 * np.cos(np.radians(3 * h_bar + 6)) - 0.20 * np.cos(np.radians(4 * h_bar - 63))
    d_theta = 30 * np.exp(-(((h_bar - 275) / 25) ** 2))
    r_c = 2 * np.sqrt(c_bar_p ** 7 / (c_bar_p ** 7 + 25 ** 7))
    s_l = 1 + 0.015 * (L_bar - 50) ** 2 / np.sqrt(20 + (L_bar - 50) ** 2)
    s_c = 1 + 0.045 * c_bar_p
    s_h = 1 + 0.015 * c_bar_p * t
    r_t = -np.sin(np.radians(2 * d_theta)) * r_c
    return float(np.sqrt((dLp / s_l) ** 2 + (dCp / s_c) ** 2 + (dHp / s_h) ** 2 + r_t * (dCp / s_c) * (dHp / s_h)))


# ---- shared preparation -----------------------------------------------------------------------

def _open(path: Path, short_side: int) -> tuple[np.ndarray, np.ndarray]:
    """Lab pixels at the given short side, and the mask of pixels that count (alpha >= 128)."""
    from PIL import Image, UnidentifiedImageError

    try:
        with Image.open(path) as im:
            im.load()
            rgba = im.convert("RGBA")
    except (OSError, UnidentifiedImageError) as exc:
        raise Failure(f"cannot read image {path}: {exc}") from exc
    w, h = rgba.size
    if min(w, h) != short_side:
        scale = short_side / min(w, h)
        rgba = rgba.resize((max(1, round(w * scale)), max(1, round(h * scale))), Image.Resampling.LANCZOS)
    arr = np.asarray(rgba, dtype=float)
    return srgb_to_lab(arr[..., :3] / 255), arr[..., 3] >= ALPHA_MIN


def _blur(channel: np.ndarray, sigma: float) -> np.ndarray:
    radius = int(np.ceil(3 * sigma))
    x = np.arange(-radius, radius + 1)
    kernel = np.exp(-(x ** 2) / (2 * sigma ** 2))
    kernel /= kernel.sum()
    padded = np.pad(channel, radius, mode="reflect")
    rows = np.apply_along_axis(lambda r: np.convolve(r, kernel, mode="valid"), 1, padded)
    return np.apply_along_axis(lambda c: np.convolve(c, kernel, mode="valid"), 0, rows)


def _ground_and_ink(lab: np.ndarray, mask: np.ndarray) -> tuple[float, np.ndarray, bool]:
    L = lab[..., 0]
    blurred = _blur(L, BLUR_SIGMA)
    counted = blurred[mask]
    if counted.size == 0:
        raise Failure("every pixel is transparent; nothing to measure")
    bins = np.clip(np.floor(counted), 0, 100).astype(int)
    hist = np.bincount(bins, minlength=101)
    mode = int(hist.argmax())
    ground = float(counted[bins == mode].mean())
    ink = (np.abs(L - ground) > INK_DELTA) & mask
    return ground, ink, hist[mode] / counted.size < GROUND_MIN_SHARE


def _share(ink: np.ndarray, mask: np.ndarray) -> float:
    n = int(mask.sum())
    return round(float(ink.sum() / n), 4) if n else 0.0


# ---- the four axes ----------------------------------------------------------------------------

def _zones(lab, mask, bands, box) -> tuple[dict, str]:
    ground, ink, unreliable = _ground_and_ink(lab, mask)
    h, w = mask.shape
    centres = (np.arange(h) + 0.5) / h * 100
    values: dict = {"bands": {}}
    for lo, hi in zip(bands, bands[1:]):
        rows = (centres > lo) & (centres <= hi) if lo > bands[0] else (centres >= lo) & (centres <= hi)
        values["bands"][f"{lo:g}-{hi:g}"] = _share(ink[rows], mask[rows])
    row_ink = ink.sum(axis=1) / np.maximum(mask.sum(axis=1), 1)
    inked = np.nonzero(row_ink >= ROW_MIN_INK)[0]
    values["first_ink_row"] = round(float(inked[0] / h * 100), 2) if inked.size else None
    values["last_ink_row"] = round(float((inked[-1] + 1) / h * 100), 2) if inked.size else None
    if box:
        x0, y0, x1, y1 = regions.to_pixels((box[0], box[1], box[2] - box[0], box[3] - box[1]), w, h)
        values["box_ink"] = _share(ink[y0:y1, x0:x1], mask[y0:y1, x0:x1])
    values["ground_L"] = round(ground, 1)
    if unreliable:
        values["ground_unreliable"] = True
    return values, f"{COMMON} bands={','.join(f'{b:g}' for b in bands)} row_min={ROW_MIN_INK} rows_on_a_boundary=upper" + (f" box={','.join(f'{b:g}' for b in box)}" if box else "")


def _contrast(lab, mask) -> tuple[dict, str]:
    ground, ink, unreliable = _ground_and_ink(lab, mask)
    values: dict = {"contrast": None, "ground_L": round(ground, 1), "ink_share": _share(ink, mask)}
    if ink.any():
        ink_l = float(lab[..., 0][ink].mean())
        values["contrast"] = round(ink_l - ground, 1)
        values["ink_L"] = round(ink_l, 1)
    if unreliable:
        values["ground_unreliable"] = True
    return values, f"{COMMON} contrast=mean(ink L*)-ground L*"


def _edges(lab, mask) -> tuple[dict, str]:
    _, ink, unreliable = _ground_and_ink(lab, mask)
    h, w = mask.shape
    b = max(1, round(EDGE_FRACTION * min(w, h)))
    ring = np.zeros_like(mask)
    ring[:b, :] = ring[-b:, :] = ring[:, :b] = ring[:, -b:] = True
    values = {"all": _share(ink[ring], mask[ring]), "top": _share(ink[:b, :], mask[:b, :]), "bottom": _share(ink[-b:, :], mask[-b:, :]), "left": _share(ink[:, :b], mask[:, :b]), "right": _share(ink[:, -b:], mask[:, -b:])}
    if unreliable:
        values["ground_unreliable"] = True
    return values, f"{COMMON} edge={EDGE_FRACTION} of short side ({b}px)"


def _kmeans(points: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(SEED)
    centres = [points[rng.integers(len(points))]]
    for _ in range(K - 1):
        d2 = np.min(((points[:, None, :] - np.array(centres)[None]) ** 2).sum(-1), axis=1)
        if d2.sum() == 0:
            break  # fewer distinct colours than K
        centres.append(points[rng.choice(len(points), p=d2 / d2.sum())])
    centres = np.array(centres)
    for _ in range(ITERATIONS):
        labels = ((points[:, None, :] - centres[None]) ** 2).sum(-1).argmin(1)
        for k in range(len(centres)):
            members = points[labels == k]
            if len(members):
                centres[k] = members.mean(0)
    labels = ((points[:, None, :] - centres[None]) ** 2).sum(-1).argmin(1)
    return centres, np.bincount(labels, minlength=len(centres)) / len(points)


def _colors(path: Path, target: str | None) -> tuple[dict, str]:
    lab, mask = _open(path, COLORS_SHORT_SIDE)
    points = lab[mask]
    if points.size == 0:
        raise Failure("every pixel is transparent; nothing to measure")
    centres, shares = _kmeans(points)
    order = np.argsort(-shares)
    clusters = [{"hex": lab_to_hex(centres[i]), "share": round(float(shares[i]), 3)} for i in order if shares[i] > 0]
    values: dict = {"clusters": clusters}
    if target:
        t_lab = srgb_to_lab(np.array([int(target[i:i + 2], 16) for i in (1, 3, 5)], dtype=float) / 255)
        distances = [delta_e2000(t_lab, centres[i]) for i in order if shares[i] > 0]
        best = int(np.argmin(distances))
        values["target"] = {"hex": target, "nearest": clusters[best]["hex"], "delta_e": round(distances[best], 2)}
    return values, f"short={COLORS_SHORT_SIDE} lanczos lab=D65 alpha<{ALPHA_MIN} excluded kmeans k={K} init=k-means++ seed={SEED} iterations={ITERATIONS}" + (" deltaE=CIEDE2000" if target else "")


def measure(images: list[Path], *, axis: str, bands: list[float] | None, box, target_color: str | None, job_dir: Path | None, version: int | None) -> dict:
    bands = bands or DEFAULT_BANDS
    results = []
    for path in images:
        if axis == "colors":
            values, fingerprint = _colors(path, target_color)
        else:
            lab, mask = _open(path, SHORT_SIDE)
            if axis == "zones":
                values, fingerprint = _zones(lab, mask, bands, box)
            elif axis == "contrast":
                values, fingerprint = _contrast(lab, mask)
            else:
                values, fingerprint = _edges(lab, mask)
        results.append({"file": str(path), "axis": axis, "values": values, "fingerprint": fingerprint})
    if job_dir is not None:
        _store(job_dir, results, axis, bands if axis == "zones" else None, box, target_color, version)
    return {"results": results}


def _store(job_dir: Path, results: list[dict], axis: str, bands, box, target_color, version) -> None:
    with ledger.edit(job_dir) as job:
        for r in results:
            path = Path(r["file"])
            name = path.name if path.parent.resolve() == job_dir.resolve() else r["file"]
            entry = {"file": name, "axis": axis, "values": r["values"], "fingerprint": r["fingerprint"], "at": ledger.now()}
            owner = version
            if owner is None:
                try:
                    owner = ledger.find_file(job, name)[0]["v"] if path.parent.resolve() == job_dir.resolve() else None
                except Failure:
                    owner = None
            if owner is not None:
                ledger.version(job, owner)
                entry["version"] = owner
            if bands:
                entry["bands"] = bands
            if box:
                entry["box"] = list(box)
            if target_color:
                entry["target_color"] = target_color
            job["measurements"].append(entry)
    sheet.rebuild(job_dir)


# ---- zoom -------------------------------------------------------------------------------------

def zoom(image: Path, *, grid: int | None, region, job_dir: Path | None) -> dict:
    from PIL import Image, UnidentifiedImageError

    try:
        with Image.open(image) as im:
            im.load()
            source = im.copy()
    except (OSError, UnidentifiedImageError) as exc:
        raise Failure(f"cannot read image {image}: {exc}") from exc
    x0, y0, x1, y1 = regions.to_pixels(region, source.width, source.height) if region else (0, 0, source.width, source.height)
    g = grid or (1 if region else 2)
    root = (job_dir if job_dir is not None else image.parent) / ".inspect" / image.name
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    out_dir = root / stamp
    out_dir.mkdir(parents=True, exist_ok=False)
    xs = [x0 + (x1 - x0) * i // g for i in range(g + 1)]
    ys = [y0 + (y1 - y0) * j // g for j in range(g + 1)]
    tiles = []
    for j in range(g):
        for i in range(g):
            box = (xs[i], ys[j], xs[i + 1], ys[j + 1])
            path = out_dir / f"r{j + 1}c{i + 1}.png"
            source.crop(box).save(path)
            tiles.append({"path": str(path), "x": box[0], "y": box[1], "w": box[2] - box[0], "h": box[3] - box[1]})
    return {"tiles": tiles}
