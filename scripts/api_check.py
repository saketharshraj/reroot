"""End-to-end check of a running ReRoot API using the demo bypass.

    uv run python scripts/api_check.py                                  # local :8080
    uv run python scripts/api_check.py https://api-152511810602.us-central1.run.app
"""

import json
import sys
import time
from pathlib import Path

import requests

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8080").rstrip("/")
DEMO = json.loads((Path(__file__).resolve().parents[1] / "web/src/fixtures/demo_project.json").read_text())


def call(method, path, **kw):
    t = time.time()
    r = requests.request(method, BASE + path, timeout=180, **kw)
    r.raise_for_status()
    print(f"{method} {path} -> {r.status_code} in {time.time() - t:.1f}s")
    return r.json()


print(call("GET", "/health?deep=true"))
region = call("GET", "/v1/regions")[0]["id"]
print("tile:", call("GET", f"/v1/layers/ndvi?region={region}")["tile_url"][:80], "...")
a = call("POST", "/v1/assess", json={"geometry": DEMO})
print(f"  footprint {a['area_ha']} ha, tree cover {a['tree_cover_ha']} ha, offset {a['offset_required_ha']} ha")
s = call("POST", "/v1/sites/search", json={
    "project_geometry": DEMO, "area_ha_needed": a["offset_required_ha"], "radius_km": 15, "region": region,
    "weights": {"establishment": 4, "water": 3, "connectivity": 3, "people": 2, "terrain": 2}})
print(f"  {len(s['sites'])} sites, {s['area_ha_selected']} ha selected of {s['cells_considered']} cells; excluded {s['cells_excluded']}")
for site in s["sites"][:5]:
    print(f"  #{site['rank']} {site['area_ha']} ha score {site['final_score']:.2f} {site['breakdown']}")
    print(f"     {site['rationale']}")
