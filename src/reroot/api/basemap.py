"""Fast plan maps: a region's Sentinel-2 basemap is rendered once by Earth Engine and kept in
Cloud Storage; plan maps crop it and draw the footprint and sites with Pillow (< 1 s)."""

import io
import math
from functools import lru_cache

import ee
import requests
from PIL import Image, ImageDraw

from reroot.api.alerts import _bucket
from reroot.core import config, layers as L, regions

WIDTH = 3000  # px across the region bbox (~10 m/px for the Aravalli box)


def _blob(region: str):
    return _bucket().blob(f"basemaps/{region}.png")


def render(region: str = config.DEFAULT_REGION, grid: int = 3) -> None:
    """One-off: render the basemap in EPSG:4326 (linear lon/lat -> pixel mapping) and store it.
    Rendered as grid x grid tiles and stitched: one big thumbnail exceeds EE's memory limit."""
    w, s, e, n = regions.spec(region)["bbox"]
    width_km = (e - w) * 111.32 * math.cos(math.radians((s + n) / 2))
    WIDTH_PX = min(WIDTH, max(1000, int(width_km * 100)))  # ~10 m per pixel
    grid = max(1, min(grid, math.ceil(WIDTH_PX / 1000)))
    height = round(WIDTH_PX * (n - s) / (e - w))
    vis = L.s2_composite(L.bbox([w, s, e, n]), "2025-11-01", "2026-03-01").visualize(**L.RGB_VIS)
    canvas = Image.new("RGB", (WIDTH_PX, height))
    xs = [round(i * WIDTH_PX / grid) for i in range(grid + 1)]
    ys = [round(j * height / grid) for j in range(grid + 1)]
    for i in range(grid):
        for j in range(grid):
            tw, th = xs[i + 1] - xs[i], ys[j + 1] - ys[j]
            tile = [w + xs[i] / WIDTH_PX * (e - w), n - ys[j + 1] / height * (n - s),
                    w + xs[i + 1] / WIDTH_PX * (e - w), n - ys[j] / height * (n - s)]
            url = vis.getThumbURL({"region": L.bbox(tile), "dimensions": f"{tw}x{th}", "format": "png", "crs": "EPSG:4326"})
            r = requests.get(url, timeout=600)
            if not r.headers.get("content-type", "").startswith("image/"):
                raise RuntimeError(f"tile {i},{j}: {r.text[:200]}")
            canvas.paste(Image.open(io.BytesIO(r.content)).convert("RGB").resize((tw, th)), (xs[i], ys[j]))
            print(f"  tile {i},{j} ok", flush=True)
    out = io.BytesIO()
    canvas.save(out, format="PNG", optimize=True)
    _blob(region).upload_from_string(out.getvalue(), content_type="image/png")


@lru_cache(maxsize=4)
def _basemap(region: str) -> Image.Image:
    return Image.open(io.BytesIO(_blob(region).download_as_bytes())).convert("RGB")


def plan_map(project: dict, sites: list[dict], region: str = config.DEFAULT_REGION, pad_m: float = 800) -> bytes:
    base = _basemap(region)
    w, s, e, n = regions.spec(region)["bbox"]
    W, H = base.size
    to_px = lambda lon, lat: ((lon - w) / (e - w) * W, (n - lat) / (n - s) * H)  # noqa: E731

    def rings(g):
        polys = [g["coordinates"]] if g["type"] == "Polygon" else g["coordinates"]
        return [poly[0] for poly in polys]

    pts = [pt for g in [project, *[x["geometry"] for x in sites]] for r in rings(g) for pt in r]
    pad_deg = pad_m / 111_000
    x0, y0 = to_px(min(p[0] for p in pts) - pad_deg, max(p[1] for p in pts) + pad_deg)
    x1, y1 = to_px(max(p[0] for p in pts) + pad_deg, min(p[1] for p in pts) - pad_deg)
    box = (max(0, int(x0)), max(0, int(y0)), min(W, int(x1)), min(H, int(y1)))

    img = base.crop(box).convert("RGBA")
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(overlay)
    local = lambda lon, lat: tuple(a - b for a, b in zip(to_px(lon, lat), box[:2]))  # noqa: E731
    for r in rings(project):
        d.polygon([local(*p) for p in r], fill=(239, 68, 68, 150), outline=(185, 28, 28, 255))
    for site in sites:
        for r in rings(site["geometry"]):
            d.polygon([local(*p) for p in r], fill=(34, 197, 94, 150), outline=(255, 255, 255, 255))
    img = Image.alpha_composite(img, overlay)
    for site in sites:  # rank badges
        cx, cy = local(*site["centroid"])
        d2 = ImageDraw.Draw(img)
        d2.ellipse([cx - 11, cy - 11, cx + 11, cy + 11], fill=(20, 83, 45, 255), outline=(255, 255, 255, 255), width=2)
        d2.text((cx, cy), str(site["rank"]), fill=(255, 255, 255, 255), anchor="mm")
    if img.width > 1600:
        img = img.resize((1600, int(img.height * 1600 / img.width)), Image.LANCZOS)
    out = io.BytesIO()
    img.convert("RGB").save(out, format="JPEG", quality=88)
    return out.getvalue()


if __name__ == "__main__":
    from reroot.core.clients import init_ee
    init_ee()
    render()
    print("basemap stored:", f"gs://{config.GCS_BUCKET}/{_blob(config.DEFAULT_REGION).name}")
