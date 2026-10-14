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

from reroot.api import alerts, assess, monitor, plan, sites
from reroot.core import config
from reroot.core.clients import gemini, gen_config, init_ee

log = logging.getLogger(__name__)
DEMO_PROJECT = json.loads((Path(__file__).resolve().parents[1] / "data/demo_project.json").read_text())
LAYERS = ["true_colour", "ndvi", "land_cover", "similarity", "rainfall", "slope"]
AVOID = ["farmland", "built", "water", "grassland", "steep", "protected"]
PRIORITIES = {"survival": "establishment", "water": "water", "connectivity": "connectivity",
              "people": "people", "terrain": "terrain"}
BASE_WEIGHTS = {"establishment": 3, "water": 2, "connectivity": 2, "people": 1, "terrain": 1}

TOOLS = [
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
    {"name": "show_map_layer", "description": "Switch the map to a satellite layer: true_colour, ndvi (vegetation health), land_cover, similarity (AlphaEarth: land like the Mangar Bani forest), rainfall, slope.",
     "parameters": {"type": "object", "properties": {"layer": {"type": "string", "enum": LAYERS}}, "required": ["layer"]}},
]

STEP_INFO = {
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
- Coverage: only the Aravalli belt between Gurugram and Faridabad is analysed in this version.
- Ecology: never suggest planting on farmland, natural grassland or wetlands; native species only;
  never Vilayati kikar (Prosopis juliflora).
- Style: short and concrete. 2-5 sentences, then bullets if useful. Use hectares (ha), t CO2e and
  Indian number formatting. Bold the key numbers. End with one suggested next step.
- Reply in the user's language (Hindi if they write in Hindi).
- The UI already shows maps, cards, the plan document and alerts; don't repeat every number,
  highlight what matters and say where to look.

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
    return json.dumps(s)


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
        req = {"project_geometry": self.ws["project"], "area_ha_needed": float(need),
               "radius_km": float(radius_km or 15), "exclude": avoid or ["farmland", "built", "water", "grassland", "steep"],
               "weights": weights, "region": config.DEFAULT_REGION}
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
    for _ in range(8):
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
