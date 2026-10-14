"""Tour of what Earth Engine can hand back for the demo area.

    uv run python scripts/ee_what_you_can_get.py

Writes into out/ee_demo/: PNG images, an animated GIF timelapse, a GeoTIFF, summary
numbers, a monthly NDVI time series, and a map tile URL for the web app.
"""

import csv
import io
import json
import sys
from pathlib import Path

import ee
import requests
from PIL import Image, ImageDraw, ImageFont, ImageSequence

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from reroot.core import config, layers as L  # noqa: E402
from reroot.core.clients import init_ee  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "out" / "ee_demo"


def save_url(url: str, path: Path) -> None:
    r = requests.get(url, timeout=300)
    r.raise_for_status()
    path.write_bytes(r.content)
    print(f"  wrote {path.relative_to(OUT.parents[1])} ({len(r.content) // 1024} KB)")


def step(name):
    def wrap(fn):
        def run(*a, **kw):
            print(f"\n== {name}")
            try:
                return fn(*a, **kw)
            except Exception as e:  # keep going so one failure doesn't hide the rest
                print(f"  FAILED: {e}")
        return run
    return wrap


@step("1. True-colour satellite image (Sentinel-2, 10 m, dry season 2025-26)")
def true_colour(aoi, s2):
    save_url(s2.visualize(**L.RGB_VIS).getThumbURL({"region": aoi, "dimensions": 1024, "format": "png"}),
             OUT / "01_true_colour.png")


@step("2. Vegetation health (NDVI)")
def ndvi_png(aoi, s2):
    save_url(L.ndvi(s2).visualize(**L.NDVI_VIS).getThumbURL({"region": aoi, "dimensions": 1024, "format": "png"}),
             OUT / "02_ndvi.png")


@step("3. Land cover (Dynamic World, 2025)")
def land_cover_png(aoi, dw):
    save_url(dw.visualize(**L.DW_VIS).getThumbURL({"region": aoi, "dimensions": 1024, "format": "png"}),
             OUT / "03_land_cover.png")


@step("4. 'Places like Mangar Bani' (AlphaEarth embeddings, 2024)")
def similarity_png(aoi, ref_pt):
    sim = L.similarity_to_point(L.embeddings(aoi, 2024), ref_pt)
    p = sim.reduceRegion(ee.Reducer.percentile([2, 98]), aoi, scale=60, maxPixels=1e9).getInfo()
    lo, hi = p["similarity_p2"], p["similarity_p98"]
    vis = {"min": lo, "max": hi, "palette": ["000004", "3b0f70", "8c2981", "de4968", "fe9f6d", "fcfdbf"]}
    marker = ee.Image().paint(ref_pt.buffer(250), 1).visualize(palette=["00ffff"])
    save_url(sim.visualize(**vis).blend(marker).getThumbURL({"region": aoi, "dimensions": 1024, "format": "png"}),
             OUT / "04_similar_to_reference.png")


@step("5. Timelapse GIF, one frame per dry season 2019-2026")
def timelapse(aoi):
    years = list(range(2019, 2027))
    frames = ee.ImageCollection([
        L.s2_composite(aoi, f"{y}-01-01", f"{y}-05-01").visualize(**L.RGB_VIS).set("year", y) for y in years
    ])
    url = frames.getVideoThumbURL({"region": aoi, "dimensions": 720, "framesPerSecond": 1, "crs": "EPSG:3857"})
    raw = requests.get(url, timeout=600)
    raw.raise_for_status()
    gif = Image.open(io.BytesIO(raw.content))
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial Bold.ttf", 36)
    except OSError:
        font = ImageFont.load_default()
    labelled = []
    for year, frame in zip(years, ImageSequence.Iterator(gif)):
        f = frame.convert("RGB")
        d = ImageDraw.Draw(f)
        d.rectangle([10, 10, 140, 60], fill=(0, 0, 0))
        d.text((22, 14), str(year), fill=(255, 255, 255), font=font)
        labelled.append(f)
    path = OUT / "05_timelapse.gif"
    labelled[0].save(path, save_all=True, append_images=labelled[1:], duration=900, loop=0)
    print(f"  wrote {path.relative_to(OUT.parents[1])} ({path.stat().st_size // 1024} KB, {len(labelled)} frames)")


@step("6. GeoTIFF download (raw NDVI values, 10 m, 3 km box) for GIS tools / numpy")
def geotiff(s2, ref_pt):
    box = ref_pt.buffer(1500).bounds()
    url = L.ndvi(s2).getDownloadURL({"region": box, "scale": 10, "format": "GEO_TIFF", "crs": "EPSG:4326"})
    save_url(url, OUT / "06_ndvi_3km.tif")


@step("7. Numbers: land-cover hectares, mean NDVI, rainfall, slope")
def stats(aoi, s2, dw):
    area = (ee.Image.pixelArea().divide(1e4).addBands(dw)
            .reduceRegion(ee.Reducer.sum().group(groupField=1, groupName="cls"),
                          aoi, scale=20, maxPixels=1e10, tileScale=4).get("groups").getInfo())
    land_cover_ha = {L.DW_CLASSES[int(g["cls"])]: round(g["sum"]) for g in area}
    means = (L.ndvi(s2).addBands(L.annual_rainfall_mm(aoi, 2025)).addBands(L.slope_deg(aoi))
             .reduceRegion(ee.Reducer.mean(), aoi, scale=100, maxPixels=1e9).getInfo())
    result = {"aoi_bbox": config.DEMO_AOI_BBOX, "land_cover_ha_2025": land_cover_ha,
              "mean_ndvi_dry_2025_26": round(means["ndvi"], 3),
              "mean_rainfall_2025_mm": round(means["rain_mm"]),
              "mean_slope_deg": round(means["slope_deg"], 1)}
    (OUT / "07_stats.json").write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


@step("8. Monthly NDVI time series for a 500 m patch (what plantation monitoring looks like)")
def ndvi_series(ref_pt):
    patch = ref_pt.buffer(500)
    months = [(y, m) for y in range(2023, 2027) for m in range(1, 13) if (y, m) <= (2026, 9)]

    def month_mean(y, m):
        start = ee.Date.fromYMD(y, m, 1)
        img = L.ndvi(L.s2_composite(patch, start, start.advance(1, "month")))
        return ee.Feature(None, {"month": f"{y}-{m:02d}",
                                 "ndvi": img.reduceRegion(ee.Reducer.mean(), patch, 10).get("ndvi")})

    rows = ee.FeatureCollection([month_mean(y, m) for y, m in months]).getInfo()["features"]
    path = OUT / "08_ndvi_monthly.csv"
    with path.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["month", "ndvi"])
        for r in rows:
            v = r["properties"].get("ndvi")
            w.writerow([r["properties"]["month"], "" if v is None else round(v, 3)])
    print(f"  wrote {path.relative_to(OUT.parents[1])} ({len(rows)} months; blanks = fully cloudy)")


@step("9. Live map tiles for the web app (XYZ template, drop into Google Maps / MapLibre)")
def tiles(s2):
    url = L.ndvi(s2).getMapId(L.NDVI_VIS)["tile_fetcher"].url_format
    (OUT / "09_tile_url.txt").write_text(url + "\n")
    print(f"  {url}")


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    init_ee()
    aoi = L.bbox(config.DEMO_AOI_BBOX)
    ref_pt = ee.Geometry.Point(config.DEMO_REFERENCE_POINT)
    s2 = L.s2_composite(aoi, "2025-11-01", "2026-03-01")
    dw = L.dynamic_world_mode(aoi, "2025-01-01", "2026-01-01")

    true_colour(aoi, s2)
    ndvi_png(aoi, s2)
    land_cover_png(aoi, dw)
    similarity_png(aoi, ref_pt)
    timelapse(aoi)
    geotiff(s2, ref_pt)
    stats(aoi, s2, dw)
    ndvi_series(ref_pt)
    tiles(s2)


if __name__ == "__main__":
    main()
