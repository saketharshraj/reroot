"""ReRoot's Gemini agent: chat in, tool calls out, results streamed as Server-Sent Events.

Stateless: the browser sends the workspace (profile, project, assessment, sites) with every
message; tools update it and stream `state` patches back. Event types:
  step       {id, tool, label, tech[]}            a tool started
  step_done  {id, ok, summary, ms}                a tool finished
  state      {patch}                              merge into the workspace
  ui         {view, ...}                          open a view / change the map
  text       {text}                               assistant reply (markdown)
  error      {message}
  done       {}
"""

import json
import logging
import time
import uuid
from datetime import date
from pathlib import Path
from typing import Iterator

from google.genai import types

from reroot.api import alerts, assess, datacenter, explore, monitor, plan, policy, sites, water
from reroot.core import config, regions
from reroot.core.clients import gemini, gen_config, init_ee

log = logging.getLogger(__name__)
DEMO_PROJECT = json.loads((Path(__file__).resolve().parents[1] / "data/demo_project.json").read_text())
LAYERS = ["true_colour", "ndvi", "land_cover", "similarity", "rainfall", "slope"]
AVOID = ["farmland", "built", "water", "grassland", "steep", "protected"]
PRIORITIES = {"survival": "establishment", "water": "water", "connectivity": "connectivity",
              "people": "people", "terrain": "terrain"}
BASE_WEIGHTS = {"establishment": 3, "water": 2, "connectivity": 2, "people": 1, "terrain": 1}

TARGETS = ["project", "sites", "site", "plantations", "plantation", "place", "area", "datacenter", "water"]

TOOLS = [
    {"name": "outline_steps", "description": "Before a request that needs 3 or more other tools, call this FIRST with the short steps you will take, so the user sees your plan.",
     "parameters": {"type": "object", "properties": {"steps": {"type": "array", "items": {"type": "string"}, "description": "3-6 short imperative steps"}}, "required": ["steps"]}},
    {"name": "research_policy", "description": "Read official government sources live (Indian forest-conservation rules, the CAF Act, Green Credit Programme, Aravalli Green Wall, State of Forest Report) and return relevant passages to answer a policy or legal question. Answer only from the returned excerpts and cite source titles.",
     "parameters": {"type": "object", "properties": {"question": {"type": "string"}, "country": {"type": "string", "description": "ISO country code, default IN"}}, "required": ["question"]}},
    {"name": "find_place", "description": "Find a village, town, landmark or road anywhere in India and fly the map there. The result says whether that place's 1-hectare grid (needed for site search and planting potential) is loaded.",
     "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}},
    {"name": "load_region", "description": "Start building the 1-hectare satellite grid for a new area (about 5-15 minutes: Earth Engine export, BigQuery grid, Vertex AI scoring). Only call after the user agrees to wait. The Studio shows progress and tells them when it is ready.",
     "parameters": {"type": "object", "properties": {"place": {"type": "string"}, "lon": {"type": "number"}, "lat": {"type": "number"},
                                                    "size_km": {"type": "number", "description": "square side, 5-25 km, default 12"}}}},
    {"name": "region_status", "description": "Check the build progress of regions being loaded.",
     "parameters": {"type": "object", "properties": {}}},
    {"name": "assess_datacenter_site", "description": "Water-positive data centre screening for a site: WRI Aqueduct water risk, GRACE groundwater trend, cooling hours, annual cooling water for evaporative/hybrid/closed-loop cooling, and nearby partners for a resource loop (treated wastewater in, clean power in, waste heat out, ponds and land for replenishment). Works anywhere in India.",
     "parameters": {"type": "object", "properties": {"place": {"type": "string"}, "lon": {"type": "number"}, "lat": {"type": "number"},
                                                    "it_load_mw": {"type": "number", "description": "IT load in MW, default 50"},
                                                    "cooling": {"type": "string", "enum": ["evaporative", "hybrid", "closed_loop"], "description": "default evaporative"},
                                                    "radius_km": {"type": "number", "description": "partner search radius, default 15"}}}},
    {"name": "analyze_area", "description": "Land cover, tree cover, carbon and plantable hectares within a radius of a place or point. Use for questions like 'what is around X' or 'how much land could we plant near Y'.",
     "parameters": {"type": "object", "properties": {"place": {"type": "string"}, "lon": {"type": "number"}, "lat": {"type": "number"},
                                                    "radius_km": {"type": "number", "description": "default 2"}}}},
    {"name": "make_timelapse", "description": "Make a Sentinel-2 satellite timelapse (one frame per year, 2019-2026) of a target to show change. Adjustable: zoom (closer/normal/wider framing), size_px (256-1024), layer (true colour or NDVI vegetation), season (dry Jan-Apr or post-monsoon Oct-Dec), years. The user can download it as a GIF.",
     "parameters": {"type": "object", "properties": {
         "target": {"type": "string", "enum": ["project", "site", "plantation", "area", "place", "water"]},
         "rank": {"type": "integer", "description": "site rank when target=site"},
         "plantation_id": {"type": "string"}, "water_project_id": {"type": "string", "description": "when target=water; omit for the last verified water body"},
         "start_year": {"type": "integer"}, "end_year": {"type": "integer"},
         "zoom": {"type": "string", "enum": ["closer", "normal", "wider"]},
         "size_px": {"type": "integer", "description": "output width, 256-1024; larger = sharper but slower"},
         "layer": {"type": "string", "enum": ["true_colour", "ndvi"]},
         "season": {"type": "string", "enum": ["dry", "post_monsoon"]}},
         "required": ["target"]}},
    {"name": "show_on_map", "description": "Point the map at something you are talking about, optionally switching the satellite layer.",
     "parameters": {"type": "object", "properties": {
         "target": {"type": "string", "enum": TARGETS}, "rank": {"type": "integer"}, "plantation_id": {"type": "string"},
         "water_project_id": {"type": "string"}, "layer": {"type": "string", "enum": LAYERS}}, "required": ["target"]}},
    {"name": "set_profile", "description": "Remember who the user is so answers fit their job. Call when they say who they are or what organisation they represent.",
     "parameters": {"type": "object", "properties": {
         "role": {"type": "string", "enum": ["developer", "forest_department", "ngo", "csr", "other"]},
         "organisation": {"type": "string"}}, "required": ["role"]}},
    {"name": "load_demo_project", "description": "Load the demo project: a 14 km highway bypass (100 m right-of-way) cutting through the Aravalli hills between Gurugram and Faridabad. Use when the user asks for the demo, mentions the bypass/road example, or has no footprint and wants to try.",
     "parameters": {"type": "object", "properties": {}}},
    {"name": "assess_project", "description": "Measure what the current project footprint will clear: land cover, tree cover, canopy height, biomass carbon, and the compensatory planting area required. Needs a footprint (drawn, uploaded or demo).",
     "parameters": {"type": "object", "properties": {}}},
    {"name": "find_planting_sites", "description": "Rank land around the project where planted trees are most likely to survive, using a 1-hectare grid in BigQuery and the Vertex AI establishment model. Clusters good cells into plantable sites until the area is met.",
     "parameters": {"type": "object", "properties": {
         "area_ha": {"type": "number", "description": "hectares needed; default = the assessed requirement"},
         "radius_km": {"type": "number", "description": "search radius around the project, 5-30 km, default 15"},
         "avoid": {"type": "array", "items": {"type": "string", "enum": AVOID}, "description": "land never to plant on; default farmland, built, water, grassland, steep"},
         "priorities": {"type": "array", "items": {"type": "string", "enum": list(PRIORITIES)}, "description": "what the user cares about most"}}}},
    {"name": "explain_site", "description": "Detailed facts about one ranked site from the last search: conditions, scores, species, why it was chosen.",
     "parameters": {"type": "object", "properties": {"rank": {"type": "integer"}}, "required": ["rank"]}},
    {"name": "generate_plan", "description": "Write the compensatory afforestation plan document (summary, sites, species, schedule, monitoring, risks, satellite map) from the current assessment and selected sites.",
     "parameters": {"type": "object", "properties": {"organisation": {"type": "string"}}}},
    {"name": "list_plantations", "description": "List monitored plantations with their satellite health status (establishment index vs target).",
     "parameters": {"type": "object", "properties": {}}},
    {"name": "check_plantation", "description": "Satellite health plus live weather risk (10-day rain forecast, rain in last 14 days, soil moisture vs normal) for one plantation.",
     "parameters": {"type": "object", "properties": {"plantation_id": {"type": "string"}}, "required": ["plantation_id"]}},
    {"name": "send_field_alerts", "description": "Run the monitoring check and publish alerts (via Pub/Sub) for plantations that need action; caretakers get a voice note in Hindi. Omit plantation_id to check all.",
     "parameters": {"type": "object", "properties": {"plantation_id": {"type": "string"}}}},
    {"name": "list_water_projects", "description": "List the water replenishment projects (lake revival, pond desilting, wetland restoration) funded to offset a data centre's water use, each with its satellite verdict: verified, partial, at_risk, not_additional, not_verified.",
     "parameters": {"type": "object", "properties": {}}},
    {"name": "verify_water_project", "description": "Check from space whether a water replenishment project really holds more water: monthly Sentinel-1 radar water area 2018-now inside the lake's outline, before vs after the intervention, with an optical cross-check for weeds. Seasons come from the lake's own 1991-2020 rainfall (rainy season, when it is fullest, its dry season), and each year is scaled for that season's rain, so it works in any climate. Pass project_id for a registered project, or a place in India / coordinates anywhere plus the intervention month.",
     "parameters": {"type": "object", "properties": {
         "project_id": {"type": "string", "description": "e.g. W1"},
         "place": {"type": "string", "description": "lake or pond name, for one not in the registry"},
         "lon": {"type": "number"}, "lat": {"type": "number"},
         "intervention": {"type": "string", "description": "YYYY-MM the works finished (required for unregistered lakes; ask the user if unknown)"},
         "depth_m": {"type": "number", "description": "storage depth used to turn extra area into volume, default 1.5"},
         "claimed_m3_yr": {"type": "number"}}}},
    {"name": "replenishment_ledger", "description": "Replenishment ledger for the sponsor's portfolio: target (120% of the data centre's freshwater use) vs claimed vs satellite-verified cubic metres per year, project by project. Uses the target of the data centre screened in this conversation if there is one.",
     "parameters": {"type": "object", "properties": {"target_m3_yr": {"type": "number", "description": "override the target"}}}},
    {"name": "show_map_layer", "description": "Switch the map to a satellite layer: true_colour, ndvi (vegetation health), land_cover, similarity (AlphaEarth: land like the Mangar Bani forest), rainfall, slope.",
     "parameters": {"type": "object", "properties": {"layer": {"type": "string", "enum": LAYERS}}, "required": ["layer"]}},
]

STEP_INFO = {
    "outline_steps": ("Planning the steps", []),
    "research_policy": ("Reading official government sources", ["Official sources", "Gemini"]),
    "find_place": ("Finding the place", ["OpenStreetMap"]),
    "load_region": ("Starting to load this region", ["Earth Engine", "BigQuery", "Vertex AI"]),
    "region_status": ("Checking region loading", ["BigQuery"]),
    "assess_datacenter_site": ("Screening water risk and resource loops", ["Earth Engine", "OpenStreetMap", "BigQuery"]),
    "analyze_area": ("Analysing the land around it", ["Earth Engine", "BigQuery"]),
    "make_timelapse": ("Rendering a satellite timelapse", ["Earth Engine"]),
    "show_on_map": ("Pointing the map", []),
    "set_profile": ("Noting who you are", []),
    "load_demo_project": ("Loading the demo bypass footprint", []),
    "assess_project": ("Measuring the footprint from satellite data", ["Earth Engine"]),
    "find_planting_sites": ("Scoring 1-ha cells and clustering sites", ["BigQuery", "Vertex AI"]),
    "explain_site": ("Pulling the site's evidence", ["BigQuery"]),
    "generate_plan": ("Writing the plan and rendering the map", ["Gemini", "Earth Engine"]),
    "list_plantations": ("Loading plantation health", ["BigQuery", "Earth Engine"]),
    "check_plantation": ("Checking satellite health and live weather", ["Earth Engine", "BigQuery"]),
    "send_field_alerts": ("Checking risk and publishing field alerts", ["Pub/Sub", "Gemini", "Text-to-Speech"]),
    "show_map_layer": ("Switching the map layer", ["Earth Engine"]),
    "list_water_projects": ("Loading replenishment projects and verdicts", ["BigQuery", "Earth Engine"]),
    "verify_water_project": ("Measuring lake water month by month from radar", ["Earth Engine", "BigQuery"]),
    "replenishment_ledger": ("Totting up claimed vs verified water", ["BigQuery"]),
}

SYSTEM = """You are ReRoot, an assistant that helps people in India plant trees where they will survive,
then prove they did. Today is {today}. You serve four kinds of users:
- developer: infrastructure/real-estate/mining companies and their consultants who must plan
  compensatory afforestation to get forest clearance;
- forest_department: officers who approve plans and track plantation survival;
- ngo: organisations that implement planting and manage caretakers;
- csr: company sustainability teams that fund planting and need proof of survival.

How to work:
- If you don't know who the user is and they haven't asked for something specific, greet them in one
  line and ask who they are and what they want to do. If they ask for something, just do it.
- ALWAYS use tools for facts and numbers. Never invent figures, places or species.
- survival_odds_percentile is a rank among nearby eligible land ("better survival odds than 97% of
  nearby land"), never a probability or a survival rate.
- The natural flow is: footprint -> assess_project -> find_planting_sites -> generate_plan, and for
  existing plantations: list_plantations -> check_plantation -> send_field_alerts.
  Chain tools when the user's request implies it (e.g. "find sites for the bypass" with no footprint:
  load_demo_project, assess_project, then find_planting_sites).
- If there is no footprint, ask them to draw it on the map or upload a KML/GeoJSON, or offer the demo bypass.
- Coverage: satellite tools (assess, area analysis, timelapse, data-centre screening, layers) work
  anywhere in India. Site search and planting potential need a region's 1-hectare grid; the Aravalli
  belt (Gurugram–Faridabad) is loaded, and users can load more. When find_place or a tool reports the
  grid isn't loaded, say so plainly, say what you CAN do right now, and offer to load it ("this takes
  about N minutes; I'll tell you when it's ready"). Only call load_region after the user agrees.
  The survival model was trained on the Aravalli belt; elsewhere call its ranking indicative.
- Data centres and water: for siting/water questions use assess_datacenter_site. Explain the loop:
  treated wastewater in, clean power in, waste heat out to nearby users, replenishment through ponds
  and land restoration, and the 120% replenishment target. Quote its assumptions; it is screening-grade.
- Replenishment verification: when someone asks whether water-positive or replenishment claims hold up,
  or about a lake/pond project, use list_water_projects, verify_water_project and replenishment_ledger.
  Don't call these for a plain data-centre screening unless the user asks about their projects or ledger.
  Say the verdict plainly and why (peak vs rain-expected, dry-season retention, green cover). Credited
  volume is indicative: extra water area x an assumed depth; satellites can't see depth or water quality.
  Open water comes from Sentinel-1 radar and green cover from Sentinel-2 optical imagery; both measure
  surface AREA, not water level or volume. Quote the evidence numbers. Seasons are found automatically
  from each lake's own rainfall (e.g. Delhi: rains Jun-Sep, fullest Aug-Nov, dry Mar-Jun; Chennai: rains
  Sep-Dec, fullest Nov-Feb); say which windows were used. Frozen lakes and snowmelt aren't modelled. For at_risk, say it needs a field check (floating weeds vs a dried, grown-over bed) and offer a
  timelapse. The demo portfolio's sponsor and claimed volumes are illustrative; the satellite readings
  are real, and Badkhal Lake's revival is a real public project. For a lake not in the registry you need
  the month the works finished; ask if the user hasn't said.
  Closing a replenishment gap: fixes for at_risk / not_verified projects come from their evidence, and
  new candidates come ONLY from assess_datacenter_site (its ponds_to_rejuvenate gives named ponds with
  added storage; call it for the sponsor's site, Gwal Pahari, if no site is in the workspace). Never
  estimate volumes, name ponds or guess causes (e.g. which weed) that no tool returned.
- Ecology: never suggest planting on farmland, natural grassland or wetlands; native species only;
  never Vilayati kikar (Prosopis juliflora).
- Style: short and concrete. 2-5 sentences, then bullets if useful. Use hectares (ha), t CO2e and
  Indian digit grouping with a dot for decimals (3,39,816 m³; 106.1 ha); never a comma as the decimal mark. Bold the key numbers. End with one suggested next step.
- Reply in the user's language (Hindi if they write in Hindi).
- Plan out loud: whenever the user asks for more than one thing, or the work needs 3+ tools, your FIRST
  call must be outline_steps. Then run tools in order (a place must be found before analysing it).
- You have NO reliable memory of laws or government schemes. For ANY question about laws, rules, policy,
  compliance, clearances, funds or schemes you MUST call research_policy first, even if you think you
  know the answer, and answer ONLY from its excerpts. Cite the
  source title for each claim (e.g. "(Van Rules, 2023)"). If excerpts don't cover it, say so. Never
  give legal advice; summarise what the documents say.
- Keep the map in sync with what you say: use show_on_map when you discuss something specific.
- Make every mention clickable with these markdown links (the UI flies the map there):
  [site 3](#site-3), [Pali scrubland block](#plantation-P3), [Mangar](#place-77.18,28.41),
  [the footprint](#project), [vegetation health](#layer-ndvi), [the plan](#view-plan),
  [the timelapse](#media), [the area analysis](#area), [plantation monitoring](#view-monitor),
  [the data centre site](#datacenter), [Badkhal Lake](#water-W1) (registered projects by id;
  a lake you checked that isn't registered is #water-adhoc), [the replenishment ledger](#view-water).
- The UI already shows maps, cards, the plan document and alerts; don't repeat every number,
  highlight what matters and say where to look.

Capabilities and limits (be honest; never pretend):
- Imagery is Sentinel-2 at 10 m per pixel, 2019-2026. You can't show individual saplings or anything
  smaller than ~10 m, and there's no imagery before 2019 in timelapses.
- Timelapse can go closer/wider, larger (up to 1024 px), NDVI instead of true colour, or post-monsoon.
  "More zoomed" = re-run make_timelapse with zoom="closer" and a larger size_px. If they ask for detail
  finer than 10 m, explain that limit and offer the closest option.
- You can't forecast beyond 10 days of weather, give legal advice, or access private land records.
- If a request is outside your tools, say so in one line and offer the nearest thing you can do.
- When you change a result on request (zoom, years, layer, weights), say what you changed.
- After make_timelapse, say what it shows (target, years, season, layer) and what to look for; don't quote
  measurements unless a tool returned them, and never call a target met that no tool said was met.

Workspace right now:
{workspace}"""


def _workspace_summary(ws: dict) -> str:
    s = {"profile": ws.get("profile"), "has_footprint": bool(ws.get("project")),
         "footprint_name": ws.get("project_name")}
    if ws.get("assess"):
        a = ws["assess"]
        s["assessment"] = {k: a.get(k) for k in ("area_ha", "tree_cover_ha", "co2e_t_est", "offset_required_ha")}
    if ws.get("sites"):
        s["sites"] = [{"rank": x["rank"], "area_ha": x["area_ha"], "score": x["final_score"]} for x in ws["sites"]]
    if ws.get("plan_id"):
        s["plan_id"] = ws["plan_id"]
    if ws.get("place"):
        s["place"] = {k: ws["place"].get(k) for k in ("name", "lon", "lat")}
    if ws.get("analysis"):
        a = ws["analysis"]
        s["area_analysis"] = {k: a.get(k) for k in ("name", "radius_km", "plantable_ha", "tree_cover_ha", "region_loaded")}
    if ws.get("media"):
        s["last_timelapse"] = {k: ws["media"].get(k) for k in ("title", "target", "rank", "plantation_id", "water_project_id", "zoom", "size_px",
                                                                "layer", "season", "years", "metres_per_pixel")}
    if ws.get("datacenter"):
        d = ws["datacenter"]
        s["datacenter"] = {"name": d.get("name"), "it_load_mw": d.get("it_load_mw"), "cooling": d.get("cooling")}
    if ws.get("water_check"):
        w = ws["water_check"]
        s["last_water_check"] = {"project_id": w.get("project_id"), "name": w.get("name"),
                                 "verdict": (w.get("evaluation") or {}).get("verdict")}
    if ws.get("water_ledger"):
        s["water_ledger"] = {k: ws["water_ledger"].get(k) for k in ("target_m3_yr", "claimed_pct_of_target", "verified_pct_of_target")}
    try:  # registered replenishment projects, so a lake named in passing maps to its id
        s["water_projects"] = [{"id": k, "name": v["name"]} for k, v in water._registry()["projects"].items()]
    except Exception:  # noqa: BLE001
        pass
    s["loaded_regions"] = [{"id": r["region_id"], "name": r["name"], "status": r["status"]} for r in regions.all_regions().values()]
    return json.dumps(s)


def _centroid(g: dict) -> tuple[float, float]:
    polys = [g["coordinates"]] if g["type"] == "Polygon" else g["coordinates"]
    pts = [pt for p in polys for pt in p[0]]
    return sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)


def _site_brief(s: dict) -> dict:
    return {"rank": s["rank"], "area_ha": s["area_ha"], "score": round(s["final_score"] * 100),
            # percentile of the model's score among eligible land nearby, NOT a probability
            "survival_odds_percentile": round(s["breakdown"]["establishment"] * 100),
            "species": [sp["local"] for sp in s["species"]], "rationale": s["rationale"]}


class Tools:
    """Each tool returns (result for Gemini, events for the UI) and may update the workspace."""

    def __init__(self, ws: dict):
        self.ws = ws

    def set_profile(self, role: str, organisation: str | None = None):
        self.ws["profile"] = {"role": role, "organisation": organisation}
        return {"ok": True}, [{"type": "state", "patch": {"profile": self.ws["profile"]}}]

    def load_demo_project(self):
        self.ws.update(project=DEMO_PROJECT, project_name="Demo: 14 km bypass", assess=None, sites=None, plan_id=None)
        return ({"ok": True, "footprint": "14 km bypass, 100 m right-of-way, Aravalli hills"},
                [{"type": "state", "patch": {"project": DEMO_PROJECT, "project_name": self.ws["project_name"],
                                             "assess": None, "sites": None}},
                 {"type": "ui", "view": "map"}])

    def assess_project(self):
        if not self.ws.get("project"):
            return {"error": "no footprint yet: ask the user to draw or upload one, or load the demo"}, []
        init_ee(high_volume=True)
        a = assess.assess(self.ws["project"])
        self.ws.update(assess=a, sites=None)
        return a, [{"type": "state", "patch": {"assess": a, "sites": None}}, {"type": "ui", "view": "map", "focus": "assess"}]

    def find_planting_sites(self, area_ha=None, radius_km=None, avoid=None, priorities=None):
        if not self.ws.get("project"):
            return {"error": "no footprint yet"}, []
        weights = dict(BASE_WEIGHTS)
        for p in priorities or []:
            weights[PRIORITIES[p]] += 2
        need = area_ha or (self.ws.get("assess") or {}).get("offset_required_ha") or 50
        lon, lat = _centroid(self.ws["project"])
        region_id = regions.containing(lon, lat)
        if not region_id:
            loading = regions.containing(lon, lat, ready_only=False)
            return ({"error": "this footprint's region is still loading" if loading else
                     "no 1-hectare grid for this area yet; offer load_region (satellite assessment still works)",
                     "center": [lon, lat]}, [])
        req = {"project_geometry": self.ws["project"], "area_ha_needed": float(need),
               "radius_km": float(radius_km or 15), "exclude": avoid or ["farmland", "built", "water", "grassland", "steep"],
               "weights": weights, "region": region_id}
        r = sites.search(req)
        self.ws.update(sites=r["sites"], site_query={k: req[k] for k in ("area_ha_needed", "radius_km", "exclude", "weights")})
        result = {"area_needed_ha": need, "area_found_ha": r["area_ha_selected"], "cells_checked": r["cells_considered"],
                  "cells_excluded_by_reason": r["cells_excluded"], "sites": [_site_brief(s) for s in r["sites"][:8]],
                  "more_sites": max(0, len(r["sites"]) - 8)}
        return result, [{"type": "state", "patch": {"sites": r["sites"], "site_query": self.ws["site_query"],
                                                    "sites_meta": {k: r[k] for k in ("area_ha_selected", "cells_considered", "cells_excluded")}}},
                        {"type": "ui", "view": "map", "focus": "sites"}]

    def explain_site(self, rank: int):
        s = next((x for x in self.ws.get("sites") or [] if x["rank"] == int(rank)), None)
        if not s:
            return {"error": f"no site with rank {rank} in the last search"}, []
        return ({**_site_brief(s), "conditions": s["conditions"], "breakdown": s["breakdown"]},
                [{"type": "ui", "view": "map", "focus": "site", "site_id": s["site_id"]}])

    def generate_plan(self, organisation=None):
        if not (self.ws.get("assess") and self.ws.get("sites")):
            return {"error": "need an assessment and a site search first"}, []
        init_ee(high_volume=True)
        profile = dict(self.ws.get("profile") or {})
        if organisation:
            profile["organisation"] = organisation
        p = plan.build(self.ws["project"], self.ws["assess"], self.ws["sites"], profile)
        self.ws["plan_id"] = p["plan_id"]
        return ({"plan_id": p["plan_id"], "totals": p["totals"], "summary": p["summary"], "risks": p["risks"]},
                [{"type": "state", "patch": {"plan": p, "plan_id": p["plan_id"]}}, {"type": "ui", "view": "plan"}])

    def list_plantations(self):
        ps = monitor.plantations()
        brief = [{k: p[k] for k in ("plantation_id", "name", "area_ha", "age_years", "status", "establishment_index",
                                    "caretaker_name", "is_demo")} for p in ps]
        return {"plantations": brief}, [{"type": "state", "patch": {"plantations": ps}}, {"type": "ui", "view": "monitor"}]

    def check_plantation(self, plantation_id: str):
        init_ee(high_volume=True)
        c = monitor.check(plantation_id)
        if not c:
            return {"error": f"unknown plantation {plantation_id}"}, []
        c = c[0]
        result = {k: c[k] for k in ("plantation_id", "name", "age_years", "status", "establishment_index",
                                    "ndvi_recent", "ndvi_expected", "weather", "alerts")}
        return result, [{"type": "state", "patch": {"plantation_check": c}},
                        {"type": "ui", "view": "monitor", "plantation_id": plantation_id}]

    def send_field_alerts(self, plantation_id=None):
        init_ee(high_volume=True)
        r = alerts.evaluate_and_publish(plantation_id)
        result = {"published": [{k: a[k] for k in ("plantation_id", "type", "severity")} | {"also": a["evidence"]["also"]}
                                for a in r["alerts"]],
                  "no_action_needed": [c["name"] for c in r["checked"] if not c["alerts"]],
                  "delivery": "Published to Pub/Sub; caretakers receive a Hindi voice note within a minute."}
        return result, [{"type": "ui", "view": "field", "expect_alerts": len(r["alerts"]),
                         "plantation_id": plantation_id}]

    def outline_steps(self, steps: list[str]):
        return {"ok": True, "note": "now carry out the steps"}, [{"type": "outline", "steps": [str(x) for x in steps][:8]}]

    def research_policy(self, question: str, country: str = "IN"):
        r = policy.research(question, country or "IN")
        if r.get("error"):
            return r, []
        return r, [{"type": "sources", "items": r["sources"]}]

    def find_place(self, query: str):
        hits = explore.find_place(query)
        if not hits:
            return {"error": f"'{query}' not found in India on OpenStreetMap"}, []
        p = hits[0]
        self.ws["place"] = p
        result = {"place": {k: p[k] for k in ("name", "label", "lon", "lat", "kind")}, "grid_loaded": p["data_loaded"],
                  "other_matches": [h["label"] for h in hits[1:3]]}
        if not p["data_loaded"]:
            loading = p.get("region_id")
            result["note"] = (f"Region {loading} is still loading." if loading else
                              "No 1-hectare grid here yet: site search and planting potential need load_region "
                              f"(~{regions.eta_minutes(regions.create_bbox(p['lon'], p['lat']))} min). Satellite tools work now.")
        return result, [{"type": "state", "patch": {"place": p}}, {"type": "ui", "view": "map", "focus": "place"}]

    def load_region(self, place=None, lon=None, lat=None, size_km=12):
        name = place or (self.ws.get("place") or {}).get("name") or "New region"
        if lon is None or lat is None:
            if place:
                hits = explore.find_place(place)
                if not hits:
                    return {"error": f"'{place}' not found"}, []
                lon, lat, name = hits[0]["lon"], hits[0]["lat"], hits[0]["name"]
            elif self.ws.get("place"):
                lon, lat = self.ws["place"]["lon"], self.ws["place"]["lat"]
            else:
                return {"error": "need a place or coordinates"}, []
        existing = regions.containing(float(lon), float(lat), ready_only=False)
        if existing and regions.spec(existing)["status"] == "failed":
            regions.start_build(existing)  # retry a failed build
            pub = regions.public(regions.spec(existing)) | {"id": existing}
            return {"region": pub, "note": "Previous build failed; retrying now."}, [{"type": "region", "region": pub}]
        if existing:
            r = regions.spec(existing)
            return {"region": regions.public(r), "note": "already loaded or loading"}, [{"type": "region", "region": regions.public(r) | {"id": existing}}]
        r = regions.create(name, float(lon), float(lat), size_km or 12)
        regions.start_build(r["region_id"])
        pub = regions.public(regions.spec(r["region_id"])) | {"id": r["region_id"]}
        return {"region": pub, "note": f"Building; about {pub['eta_minutes']} minutes."}, [{"type": "region", "region": pub}]

    def region_status(self):
        return {"regions": [regions.public(r) for r in regions.all_regions(fresh=True).values()]}, []

    def assess_datacenter_site(self, place=None, lon=None, lat=None, it_load_mw=50, cooling="evaporative", radius_km=15):
        name = place
        if place and (lon is None or lat is None):
            hits = explore.find_place(place)
            if not hits:
                return {"error": f"'{place}' not found"}, []
            lon, lat, name = hits[0]["lon"], hits[0]["lat"], hits[0]["name"]
            self.ws["place"] = hits[0]
        if lon is None and self.ws.get("place"):
            lon, lat, name = self.ws["place"]["lon"], self.ws["place"]["lat"], self.ws["place"]["name"]
        if lon is None:
            return {"error": "need a place or coordinates for the site"}, []
        init_ee(high_volume=True)
        d = datacenter.assess_site(float(lon), float(lat), name, float(it_load_mw or 50), cooling or "evaporative", float(radius_km or 15))
        self.ws["datacenter"] = d
        brief = {k: d[k] for k in ("name", "it_load_mw", "cooling", "water_risk", "climate", "water_ml_per_mw_yr", "chosen",
                                   "partner_counts", "assumptions")}
        brief["flows"] = [{"kind": f["kind"], "label": f["label"],
                           "partner": (f["from"] if isinstance(f["from"], dict) else f["to"])["name"],
                           "distance_km": (f["from"] if isinstance(f["from"], dict) else f["to"])["distance_km"]} for f in d["flows"]]
        patch = {"datacenter": d} | ({"place": self.ws["place"]} if self.ws.get("place") else {})
        return brief, [{"type": "state", "patch": patch}, {"type": "ui", "view": "map", "focus": "datacenter"}]

    def analyze_area(self, place=None, lon=None, lat=None, radius_km=None):
        name = place
        if place and (lon is None or lat is None):
            hits = explore.find_place(place)
            if not hits:
                return {"error": f"'{place}' not found inside the analysed region"}, []
            lon, lat, name = hits[0]["lon"], hits[0]["lat"], hits[0]["name"]
            self.ws["place"] = hits[0]
        if lon is None and self.ws.get("place"):
            lon, lat, name = self.ws["place"]["lon"], self.ws["place"]["lat"], self.ws["place"]["name"]
        if lon is None:
            return {"error": "need a place name or coordinates"}, []
        init_ee(high_volume=True)
        a = explore.analyze(float(lon), float(lat), float(radius_km or 2), name)
        self.ws["analysis"] = a
        patch = {"analysis": a} | ({"place": self.ws["place"]} if self.ws.get("place") else {})
        return ({k: v for k, v in a.items() if k != "geometry"},
                [{"type": "state", "patch": patch}, {"type": "ui", "view": "map", "focus": "area"}])

    def list_water_projects(self):
        ps = water.projects()
        brief = [{k: p[k] for k in ("project_id", "name", "place", "intervention", "intervention_date", "verdict",
                                    "evidence", "claimed_m3_yr", "credited_m3_yr", "is_demo")} for p in ps]
        return ({"projects": brief, "sponsor": ps[0]["sponsor"] if ps else None},
                [{"type": "state", "patch": {"water_projects": ps}}, {"type": "ui", "view": "water"}])

    def verify_water_project(self, project_id=None, place=None, lon=None, lat=None, intervention=None,
                             depth_m=None, claimed_m3_yr=None):
        if not project_id and place and not intervention:
            project_id = water.match(place)  # a registered project named in passing
        if project_id:
            v = water.verify(str(project_id).upper())
            if not v:
                return {"error": f"unknown water project {project_id}; call list_water_projects"}, []
        else:
            name = place
            if place and (lon is None or lat is None):
                hits = explore.find_place(place)
                if not hits:
                    return {"error": f"'{place}' not found"}, []
                lon, lat, name = hits[0]["lon"], hits[0]["lat"], hits[0]["name"]
            if lon is None:
                return {"error": "need project_id, or a lake name / coordinates"}, []
            if not intervention:
                return {"error": "need the month the works finished (YYYY-MM) to set the before/after split; ask the user"}, []
            init_ee(high_volume=True)
            v = water.verify_adhoc(float(lon), float(lat), str(intervention)[:7], name, float(depth_m or water.DEFAULT_DEPTH_M),
                                   claimed_m3_yr)
            if v.get("error"):
                return v, []
        self.ws["water_check"] = v
        ev = v["evaluation"]
        brief = {k: v.get(k) for k in ("project_id", "name", "place", "intervention", "intervention_date", "outline_ha",
                                       "claimed_m3_yr", "is_demo", "notes")}
        brief |= {k: ev.get(k) for k in ("verdict", "verdict_text", "note", "baseline_years", "baseline", "judged_year",
                                         "recent", "credited_m3_yr", "radar_optical_agreement", "assumptions")}
        se = ev.get("season") or {}
        brief["season"] = {k: se.get(k) for k in ("water_year", "rain", "peak", "dry", "weak", "source")}
        brief["post_years"] = [{k: h.get(k) for k in ("label", "peak_ha", "expected_peak_ha", "dry_ha", "dry_gain_ha",
                                                      "wet_months", "season_rain_mm", "rain_vs_baseline", "green_dry_share")}
                               for h in ev["years"] if h["label"] in ev.get("post_years", [])]
        return brief, [{"type": "state", "patch": {"water_check": v}},
                       {"type": "ui", "view": "water", "focus": "water", "water_project_id": v.get("project_id") or "adhoc"}]

    def replenishment_ledger(self, target_m3_yr=None):
        source = None
        dc = (self.ws.get("datacenter") or {}).get("chosen") or {}
        if not target_m3_yr and dc.get("replenishment_target_m3"):
            target_m3_yr = dc["replenishment_target_m3"]
            source = f"120% of freshwater use for {self.ws['datacenter'].get('name') or 'the screened site'} (this conversation)"
        led = water.ledger(target_m3_yr, target_source=source or ("user-supplied" if target_m3_yr else None))
        if led.get("error"):
            return led, []
        self.ws["water_ledger"] = led
        return ({k: v for k, v in led.items() if k != "sources"},
                [{"type": "state", "patch": {"water_ledger": led, "water_projects": water.projects()}}, {"type": "ui", "view": "water"}])

    def _water_target(self, water_project_id=None) -> dict | None:
        if water_project_id:
            return water.verify(str(water_project_id).upper())
        return self.ws.get("water_check")

    def _target_geoms(self, target, rank=None, plantation_id=None, water_project_id=None) -> tuple[list[dict], str]:
        if target == "water":
            w = self._water_target(water_project_id)
            return ([w["geometry"]], w["name"]) if w else ([], "")
        if target == "project" and self.ws.get("project"):
            return [self.ws["project"]], self.ws.get("project_name") or "Project footprint"
        if target == "site" and self.ws.get("sites"):
            s = next((x for x in self.ws["sites"] if x["rank"] == int(rank or 1)), None)
            if s:
                return [s["geometry"]], f"Site {s['rank']}"
        if target == "plantation":
            p = next((x for x in monitor.plantations() if x["plantation_id"] == plantation_id), None)
            if p:
                return [p["geometry"]], p["name"]
        if target == "area" and self.ws.get("analysis"):
            return [self.ws["analysis"]["geometry"]], f"Around {self.ws['analysis'].get('name') or 'point'}"
        if target in ("place", "area") and self.ws.get("place"):
            pl = self.ws["place"]
            return [explore.circle(pl["lon"], pl["lat"], 1.0)], pl["name"]
        return [], ""

    def make_timelapse(self, target: str, rank=None, plantation_id=None, start_year=2019, end_year=2026,
                       zoom="normal", size_px=520, layer="true_colour", season="dry", water_project_id=None):
        geoms, title = self._target_geoms(target, rank, plantation_id, water_project_id)
        if not geoms:
            return {"error": f"nothing to show for target '{target}' yet"}, []
        init_ee(high_volume=True)
        m = explore.timelapse(geoms, title, start_year or 2019, end_year or 2026, zoom or "normal",
                              size_px or 520, layer or "true_colour", season or "dry")
        m = m | {"target": target, "rank": rank, "plantation_id": plantation_id,  # so "zoom in" can redo the same target
                 "water_project_id": water_project_id or (self.ws.get("water_check") or {}).get("project_id") if target == "water" else None}
        self.ws["media"] = m
        return ({"ok": True} | {k: m[k] for k in ("title", "years", "zoom", "size_px", "layer", "season",
                                                   "metres_per_pixel", "native_resolution_m")},
                [{"type": "state", "patch": {"media": m}}] + self._focus_events(target, rank, plantation_id, water_project_id=water_project_id))

    def _focus_events(self, target, rank=None, plantation_id=None, layer=None, water_project_id=None) -> list[dict]:
        ev: dict = {"type": "ui", "view": "monitor" if target.startswith("plantation") else "water" if target == "water" else "map"}
        if layer:
            ev["layer"] = layer
        if target == "site":
            s = next((x for x in self.ws.get("sites") or [] if x["rank"] == int(rank or 1)), None)
            ev |= {"focus": "site", "site_id": s["site_id"]} if s else {"focus": "sites"}
        elif target == "plantation":
            ev |= {"plantation_id": plantation_id}
        elif target == "sites":
            ev["focus"] = "sites"
        elif target == "project":
            ev["focus"] = "assess"
        elif target in ("place", "area", "datacenter"):
            ev["focus"] = target
        elif target == "water":
            ev |= {"focus": "water", "water_project_id": water_project_id or (self.ws.get("water_check") or {}).get("project_id")}
        return [ev]

    def show_on_map(self, target: str, rank=None, plantation_id=None, layer=None, water_project_id=None):
        return {"ok": True}, self._focus_events(target, rank, plantation_id, layer, water_project_id)

    def show_map_layer(self, layer: str):
        return {"ok": True}, [{"type": "ui", "view": "map", "layer": layer}]


def _contents(history: list[dict], message: str) -> list[types.Content]:
    out = []
    for h in history[-12:]:
        if h.get("text"):
            out.append(types.Content(role="user" if h["role"] == "user" else "model", parts=[types.Part(text=h["text"])]))
    out.append(types.Content(role="user", parts=[types.Part(text=message)]))
    return out


def run(message: str, history: list[dict], workspace: dict) -> Iterator[dict]:
    tools = Tools(dict(workspace))
    contents = _contents(history, message)
    decls = [types.FunctionDeclaration(name=t["name"], description=t["description"], parameters_json_schema=t["parameters"])
             for t in TOOLS]
    for _ in range(12):
        cfg = gen_config(
            system_instruction=SYSTEM.format(today=date.today().isoformat(), workspace=_workspace_summary(tools.ws)),
            tools=[types.Tool(function_declarations=decls)],
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )
        try:
            resp = gemini().models.generate_content(model=config.GEMINI_MODEL_FAST, contents=contents, config=cfg)
        except Exception as e:  # noqa: BLE001
            log.exception("gemini call failed")
            yield {"type": "error", "message": f"Gemini call failed: {e}"}
            return
        content = resp.candidates[0].content
        contents.append(content)  # keeps thought signatures for the next call
        calls = [p.function_call for p in content.parts or [] if p.function_call]
        if not calls:
            text = "".join(p.text for p in content.parts or [] if p.text and not getattr(p, "thought", False))
            yield {"type": "text", "text": text.strip()}
            yield {"type": "done"}
            return
        responses = []
        for call in calls:
            sid = uuid.uuid4().hex[:8]
            label, tech = STEP_INFO.get(call.name, (call.name, []))
            yield {"type": "step", "id": sid, "tool": call.name, "label": label, "tech": tech, "args": dict(call.args or {})}
            t0 = time.time()
            try:
                result, events = getattr(tools, call.name)(**(call.args or {}))
                ok = "error" not in result
            except Exception as e:  # noqa: BLE001
                log.exception("tool %s failed", call.name)
                result, events, ok = {"error": str(e)}, [], False
            yield from events
            yield {"type": "step_done", "id": sid, "ok": ok, "ms": int((time.time() - t0) * 1000),
                   "summary": result.get("error") if not ok else None}
            responses.append(types.Part(function_response=types.FunctionResponse(
                id=call.id, name=call.name, response={"result": result})))
        contents.append(types.Content(role="user", parts=responses))
    yield {"type": "text", "text": "I ran out of steps for this request. Try asking for one thing at a time."}
    yield {"type": "done"}
