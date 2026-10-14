"""Reusable Earth Engine layers. Every function returns a lazy ee object; nothing is
computed until you call getInfo / getThumbURL / export on it."""

import ee

S2 = "COPERNICUS/S2_SR_HARMONIZED"
CLOUD_SCORE_PLUS = "GOOGLE/CLOUD_SCORE_PLUS/V1/S2_HARMONIZED"
DYNAMIC_WORLD = "GOOGLE/DYNAMICWORLD/V1"
EMBEDDINGS = "GOOGLE/SATELLITE_EMBEDDING/V1/ANNUAL"
CHIRPS = "UCSB-CHG/CHIRPS/DAILY"
DEM = "COPERNICUS/DEM/GLO30_2024_1"

DW_CLASSES = ["water", "trees", "grass", "flooded_vegetation", "crops",
              "shrub_and_scrub", "built", "bare", "snow_and_ice"]
DW_PALETTE = ["419bdf", "397d49", "88b053", "7a87c6", "e49635",
              "dfc35a", "c4281b", "a59b8f", "b39fe1"]
NDVI_PALETTE = ["8c510a", "d8b365", "f6e8c3", "c7eae5", "5ab4ac", "01665e"]

RGB_VIS = {"bands": ["B4", "B3", "B2"], "min": 0, "max": 2500, "gamma": 1.2}
NDVI_VIS = {"min": 0.0, "max": 0.8, "palette": NDVI_PALETTE}
DW_VIS = {"min": 0, "max": 8, "palette": DW_PALETTE}


def bbox(coords: list[float]) -> ee.Geometry:
    return ee.Geometry.Rectangle(coords)


def s2_composite(aoi: ee.Geometry, start: str, end: str, clear_threshold: float = 0.6) -> ee.Image:
    """Cloud-masked Sentinel-2 median composite (Cloud Score+ masking)."""
    cs = ee.ImageCollection(CLOUD_SCORE_PLUS)
    return (ee.ImageCollection(S2)
            .filterBounds(aoi).filterDate(start, end)
            .linkCollection(cs, ["cs_cdf"])
            .map(lambda img: img.updateMask(img.select("cs_cdf").gte(clear_threshold)))
            .median()
            .clip(aoi))


def ndvi(s2_img: ee.Image) -> ee.Image:
    return s2_img.normalizedDifference(["B8", "B4"]).rename("ndvi")


def dynamic_world_mode(aoi: ee.Geometry, start: str, end: str) -> ee.Image:
    """Most frequent Dynamic World class per pixel over the period."""
    return (ee.ImageCollection(DYNAMIC_WORLD)
            .filterBounds(aoi).filterDate(start, end)
            .select("label").mode().clip(aoi))


def embeddings(aoi: ee.Geometry, year: int) -> ee.Image:
    """AlphaEarth 64-band embedding mosaic for a year (bands A00..A63, unit length)."""
    return (ee.ImageCollection(EMBEDDINGS)
            .filterBounds(aoi)
            .filterDate(f"{year}-01-01", f"{year + 1}-01-01")
            .mosaic().clip(aoi))


def similarity_to_point(emb: ee.Image, point: ee.Geometry) -> ee.Image:
    """Cosine similarity of every pixel's embedding to the embedding at `point`.
    Embeddings are unit length, so the dot product is the cosine similarity."""
    ref = emb.sample(point, scale=10).first().toDictionary()
    ref_img = ee.Image.constant(ref.values(emb.bandNames())).rename(emb.bandNames())
    return emb.multiply(ref_img).reduce(ee.Reducer.sum()).rename("similarity")


def annual_rainfall_mm(aoi: ee.Geometry, year: int) -> ee.Image:
    return (ee.ImageCollection(CHIRPS)
            .filterDate(f"{year}-01-01", f"{year + 1}-01-01")
            .sum().clip(aoi).rename("rain_mm"))


def slope_deg(aoi: ee.Geometry) -> ee.Image:
    dem = ee.ImageCollection(DEM).select("DEM").filterBounds(aoi).mosaic()
    return ee.Terrain.slope(dem.setDefaultProjection("EPSG:4326", None, 30)).clip(aoi).rename("slope_deg")
