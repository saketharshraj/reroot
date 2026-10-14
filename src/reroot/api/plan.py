"""Compensatory planting plan. Numbers and schedule are computed here; Gemini only writes the
executive summary and risks from those facts. The map is an Earth Engine satellite render."""

import json
import math
import uuid
from datetime import date, datetime, timezone

import ee

from reroot.api import basemap
from reroot.api.alerts import _bucket
from reroot.core import config, layers as L
from reroot.core.clients import gemini, gen_config

SAPLINGS_PER_HA = 1100  # 3 m x 3 m spacing


def _dist_km(lon1, lat1, lon2, lat2) -> float:
    p = math.pi / 180
    a = (math.sin((lat2 - lat1) * p / 2) ** 2
         + math.cos(lat1 * p) * math.cos(lat2 * p) * math.sin((lon2 - lon1) * p / 2) ** 2)
    return 12742 * math.asin(math.sqrt(a))


def _vertices(g: dict):
    rings = g["coordinates"] if g["type"] == "Polygon" else [r for poly in g["coordinates"] for r in poly]
    return [pt for ring in rings for pt in ring]


def _schedule(today: date) -> list[dict]:
    y = today.year + (1 if today.month >= 6 else 0)  # next monsoon planting season
    return [
        {"when": f"Now – Dec {today.year}", "what": "Submit plan; verify land records and visit each site on the ground."},
        {"when": f"Jan – Apr {y}", "what": "Raise or procure native saplings from local nurseries."},
        {"when": f"May – Jun {y}", "what": "Prepare sites: pits, moisture-conservation trenches, fencing or tree guards."},
        {"when": f"Jul – Aug {y}", "what": "Plant with the monsoon rains."},
        {"when": f"Oct {y} – Jun {y + 2}", "what": "Dry-season watering for two years, triggered by ReRoot dry-spell alerts."},
        {"when": f"Jul {y + 1}", "what": "Replace saplings that died in the first year."},
        {"when": "Every month", "what": "Satellite health check of every site; caretaker photo audit each quarter."},
    ]


MONITORING = [
    "Monthly Sentinel-2 greenness of each site compared with a nearby mature forest, adjusted for age.",
    "Live rain forecast and soil-moisture checks; alerts go to caretakers as voice notes in Hindi.",
    "Caretaker photos verified for location and sapling condition, logged as audit evidence.",
    "Annual survival report per site for the forest department and funders.",
]


def _map_url(plan_id: str, project: dict, sites: list[dict]) -> str | None:
    """Fast path: crop the stored region basemap and draw on it. Fallback: live EE thumbnail."""
    try:
        jpg = basemap.plan_map(project, sites)
        _bucket().blob(f"plans/{plan_id}.jpg").upload_from_string(jpg, content_type="image/jpeg")
        return f"/v1/plans/{plan_id}/map.jpg"
    except Exception:  # noqa: BLE001 - basemap not rendered yet; use Earth Engine directly
        pass
    try:
        pfc = ee.FeatureCollection([ee.Feature(ee.Geometry(project))])
        sfc = ee.FeatureCollection([ee.Feature(ee.Geometry(s["geometry"])) for s in sites])
        region = pfc.merge(sfc).geometry().bounds().buffer(1000).bounds()
        img = (L.s2_composite(region, "2025-11-01", "2026-03-01").visualize(**L.RGB_VIS)
               .blend(ee.Image().byte().paint(pfc, 1).visualize(palette=["ef4444"], opacity=0.75))
               .blend(ee.Image().byte().paint(sfc, 1).visualize(palette=["22c55e"], opacity=0.7))
               .blend(ee.Image().byte().paint(sfc, 1, 2).visualize(palette=["ffffff"])))
        return img.getThumbURL({"region": region, "dimensions": 1400, "format": "png"})
    except Exception:  # noqa: BLE001 - the plan is still useful without the picture
        return None


def build(project: dict, assess: dict, sites: list[dict], profile: dict | None = None) -> dict:
    profile = profile or {}
    verts = _vertices(project)
    rows, species = [], {}
    for s in sites:
        lon, lat = s["centroid"]
        rows.append({
            "rank": s["rank"], "area_ha": s["area_ha"], "score": round(s["final_score"] * 100),
            "survival_rank_pct": round(s["breakdown"].get("establishment", 0) * 100),
            "distance_km": round(min(_dist_km(lon, lat, x, y) for x, y in verts), 1),
            "centroid": s["centroid"], "species": [sp["local"] for sp in s.get("species", [])],
            "rationale": s.get("rationale", ""),
        })
        for sp in s.get("species", []):
            species.setdefault(f"{sp['local']} ({sp['name']})", 0)
            species[f"{sp['local']} ({sp['name']})"] += 1
    area = round(sum(r["area_ha"] for r in rows), 1)
    need = assess.get("offset_required_ha") or 0
    totals = {"sites": len(rows), "area_ha": area, "saplings": int(area * SAPLINGS_PER_HA),
              "coverage_pct": round(area / need * 100) if need else None}

    facts = {
        "prepared_for": profile.get("organisation") or profile.get("role"),
        "region": "Aravalli belt, Gurugram–Faridabad",
        "project": {k: assess.get(k) for k in ("area_ha", "tree_cover_ha", "forest_like_ha", "co2e_t_est", "canopy_height_mean_m")},
        "requirement_ha": need, "requirement_ha_degraded_land": assess.get("offset_required_ha_degraded"),
        "selected": totals,
        "sites": [{k: r[k] for k in ("rank", "area_ha", "score", "survival_rank_pct", "distance_km", "species")} for r in rows],
    }
    resp = gemini().models.generate_content(
        model=config.GEMINI_MODEL_FAST,
        contents=("Write the executive summary (4 sentences, plain English, for a forest department reviewer) "
                  "and the top 3 risks with mitigations for this compensatory afforestation plan. Use ONLY these "
                  "facts and numbers; do not invent any others.\n" + json.dumps(facts)),
        config=gen_config(response_mime_type="application/json", response_schema={
            "type": "object", "properties": {
                "summary": {"type": "string"},
                "risks": {"type": "array", "items": {"type": "object", "properties": {
                    "risk": {"type": "string"}, "mitigation": {"type": "string"}}, "required": ["risk", "mitigation"]}}},
            "required": ["summary", "risks"]}),
    )
    words = json.loads(resp.text)

    plan_id = uuid.uuid4().hex[:12]
    plan = {
        "plan_id": plan_id,
        "title": "Compensatory afforestation plan",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "prepared_for": facts["prepared_for"],
        "region": facts["region"],
        "summary": words["summary"],
        "project": facts["project"] | {"land_cover_ha": assess.get("land_cover_ha", {})},
        "requirement": {"offset_required_ha": need, "offset_required_ha_degraded": assess.get("offset_required_ha_degraded")},
        "totals": totals,
        "sites": rows,
        "species_mix": [{"species": k, "sites": v} for k, v in sorted(species.items(), key=lambda kv: -kv[1])],
        "schedule": _schedule(date.today()),
        "monitoring": MONITORING,
        "risks": words["risks"],
        "map_image_url": _map_url(plan_id, project, sites),
        "assumptions": [f"{SAPLINGS_PER_HA:,} saplings per hectare (3 m × 3 m spacing).",
                        "Compensatory area = trees + scrub inside the footprint (1:1 on non-forest land, 2:1 on degraded forest land).",
                        "Survival rank = percentile of the establishment model's score among eligible land in the search radius."],
        "sources": sorted(set(assess.get("sources", [])) | {src for s in sites for src in s.get("sources", [])}),
    }
    _bucket().blob(f"plans/{plan['plan_id']}.json").upload_from_string(json.dumps(plan), content_type="application/json")
    return plan
