"""Impact of clearing a footprint, computed live in Earth Engine (one round trip)."""

import json
from functools import lru_cache

import ee

from reroot.core import layers as L

CANOPY_HEIGHT = "projects/meta-forest-monitoring-okw37/assets/CanopyHeight"  # Meta/WRI 1 m, band cover_code = metres
GEDI_AGBD = "LARSE/GEDI/GEDI04_B_002"  # GEDI L4B gridded above-ground biomass density, Mg/ha, 1 km
CARBON_FRACTION = 0.47  # IPCC default carbon fraction of dry biomass
CO2_PER_C = 44 / 12


def assess(geojson: dict, year: int = 2025) -> dict:
    return _assess_cached(json.dumps(geojson, sort_keys=True), year)


@lru_cache(maxsize=128)
def _assess_cached(key: str, year: int) -> dict:
    g = ee.Geometry(json.loads(key))
    dw = L.dynamic_world_mode(g.buffer(50), f"{year}-01-01", f"{year + 1}-01-01")
    land_cover = (ee.Image.pixelArea().divide(1e4).addBands(dw)
                  .reduceRegion(ee.Reducer.sum().group(1, "cls"), g, 10, maxPixels=1e9).get("groups"))
    height = ee.ImageCollection(CANOPY_HEIGHT).filterBounds(g).mosaic()
    height_mean = height.updateMask(height.gte(2)).reduceRegion(ee.Reducer.mean(), g, 10, maxPixels=1e9).get("cover_code")
    agbd = ee.Image(GEDI_AGBD).select("MU").reduceRegion(ee.Reducer.mean(), g, 100, maxPixels=1e9).get("MU")

    r = ee.Dictionary({"area_ha": g.area(1).divide(1e4), "lc": land_cover, "h": height_mean, "agbd": agbd}).getInfo()

    lc = {L.DW_CLASSES[int(x["cls"])]: round(x["sum"], 2) for x in r["lc"] or []}
    tree_ha = lc.get("trees", 0.0)
    forest_like_ha = tree_ha + lc.get("shrub_and_scrub", 0.0)
    agb_t = r["agbd"] * r["area_ha"] if r.get("agbd") is not None else None
    carbon_t = agb_t * CARBON_FRACTION if agb_t is not None else None
    return {
        "area_ha": round(r["area_ha"], 2),
        "tree_cover_ha": round(tree_ha, 2),
        "forest_like_ha": round(forest_like_ha, 2),
        "land_cover_ha": lc,
        "canopy_height_mean_m": round(r["h"], 1) if r.get("h") is not None else None,
        "agb_t": round(agb_t) if agb_t is not None else None,
        "carbon_t_est": round(carbon_t) if carbon_t is not None else None,
        "co2e_t_est": round(carbon_t * CO2_PER_C) if carbon_t is not None else None,
        # Compensatory afforestation: equal area of non-forest land, or twice the area of degraded
        # forest land. Trees + scrub stand in for legally notified forest land here.
        "offset_required_ha": round(forest_like_ha, 1),
        "offset_required_ha_degraded": round(forest_like_ha * 2, 1),
        "sources": [L.DYNAMIC_WORLD, CANOPY_HEIGHT, GEDI_AGBD],
    }
