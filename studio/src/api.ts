// ReRoot API client for Studio. Contract: contracts/openapi.yaml.

export const API_BASE =
  (import.meta.env.VITE_API_BASE_URL as string | undefined)?.replace(/\/$/, "") ?? "http://localhost:8080";

export type Geometry = GeoJSON.Polygon | GeoJSON.MultiPolygon;
export type LayerId = "true_colour" | "ndvi" | "land_cover" | "similarity" | "rainfall" | "slope";
export type Role = "developer" | "forest_department" | "ngo" | "csr" | "other";

export interface Profile { role: Role; organisation?: string | null }

export interface Assess {
  area_ha: number; tree_cover_ha: number; forest_like_ha: number; land_cover_ha: Record<string, number>;
  canopy_height_mean_m: number | null; co2e_t_est: number | null; carbon_t_est: number | null;
  offset_required_ha: number; offset_required_ha_degraded: number; sources: string[];
}

export interface Species { name: string; local: string; why: string }

export interface Site {
  site_id: string; rank: number; geometry: Geometry; centroid: [number, number]; area_ha: number;
  final_score: number; breakdown: Record<string, number>; conditions: Record<string, number>;
  species: Species[]; rationale: string; sources: string[];
}

export interface PlanSiteRow {
  rank: number; area_ha: number; score: number; survival_rank_pct: number; distance_km: number;
  species: string[]; rationale: string;
}

export interface Plan {
  plan_id: string; title: string; generated_at: string; prepared_for: string | null; region: string;
  summary: string; project: Record<string, number | null | Record<string, number>>;
  requirement: { offset_required_ha: number; offset_required_ha_degraded: number };
  totals: { sites: number; area_ha: number; saplings: number; coverage_pct: number | null };
  sites: PlanSiteRow[]; species_mix: { species: string; sites: number }[];
  schedule: { when: string; what: string }[]; monitoring: string[];
  risks: { risk: string; mitigation: string }[]; map_image_url: string | null;
  assumptions: string[]; sources: string[];
}

export interface Weather {
  precip_next_10d_mm: number | null; precip_last_14d_mm: number | null; soil_moisture: number | null;
  soil_moisture_vs_normal_pct: number | null; dry_spell: boolean; forecast_run: string; sources: string[];
}

export interface Plantation {
  plantation_id: string; name: string; planted_date: string; age_years: number; species: string[];
  saplings_planted: number; caretaker_name: string; language: string; source_project: string; is_demo: boolean;
  geometry: Geometry; area_ha: number; centroid: [number, number]; ndvi_recent: number | null;
  ndvi_expected: number | null; establishment_index: number | null;
  status: "on_track" | "watch" | "at_risk" | "unknown"; sources: string[];
  weather?: Weather; alerts?: { type: string; severity: string }[];
}

export interface Alert {
  alert_id: string; plantation_id: string; created_at: string; type: string; severity: string;
  title: string; message_en: string; message_local: string; evidence: Record<string, unknown>;
  has_audio: boolean; delivered: boolean;
}

export interface Verification {
  verification_id: string; plantation_id: string; is_plantation_photo: boolean;
  live_saplings_est: number; dead_or_dry_est: number; health: string; signs?: string[];
  summary: string; confidence: number;
  location: { source: string; match: boolean | null; distance_m: number | null };
}

export interface Place { name: string; label?: string; lon: number; lat: number; kind?: string }

export interface AreaAnalysis {
  name: string | null; center: [number, number]; radius_km: number; geometry: Geometry; area_ha: number;
  land_cover_pct: Record<string, number>; tree_cover_ha: number; co2e_t_est: number | null;
  canopy_height_mean_m: number | null; plantable_ha: number; plantable_top_quartile_survival_ha: number;
  ndvi_dry_mean: number | null; sources: string[];
}

export interface Media {
  url: string; title: string; years: [number, number]; bbox: number[];
  zoom?: string; size_px?: number; layer?: string; season?: string; metres_per_pixel?: number; native_resolution_m?: number;
}

export interface RegionInfo {
  id: string; region_id: string; name: string; bbox: [number, number, number, number];
  status: "queued" | "exporting" | "building" | "scoring" | "rendering" | "ready" | "failed";
  progress: number; message: string | null; built_in: boolean; eta_minutes: number | null;
}

export interface Partner {
  name: string; category: "reclaimed_water" | "clean_power" | "heat_user" | "water_body" | "data_centre";
  lon: number; lat: number; distance_km: number; area_m2: number | null; osm: string;
}

export interface Flow { from: Partner | "site"; to: Partner | "site"; kind: "reclaimed_water" | "clean_power" | "heat" | "replenish"; label: string }

export interface DataCentre {
  name: string | null; site: [number, number]; it_load_mw: number; cooling: "evaporative" | "hybrid" | "closed_loop"; radius_km: number;
  water_risk: Record<string, string | number | null>;
  climate: { hours_above_25c: number; hours_above_35c: number; share_hours_needing_evaporative: number; solar_kwh_per_m2_yr: number | null };
  water_m3_per_yr: Record<string, number>; water_ml_per_mw_yr: Record<string, number>;
  chosen: {
    total_m3: number; reclaimed_m3: number; freshwater_m3: number; replenishment_target_m3: number;
    ponds_to_rejuvenate: { name: string; distance_km: number; area_ha: number; added_storage_m3: number }[];
    replenishment_covered_by_ponds: boolean; ponds_nearby_count: number; plantable_ha_nearby: number | null;
  };
  partners: Partner[]; partner_counts: Record<string, number>; flows: Flow[]; assumptions: string[]; sources: string[];
}

export interface PolicySource {
  id: string; title: string; publisher: string; url: string; live: boolean; fetched_at: string | null;
  available: boolean; tls_verified: boolean;
}

export interface Workspace {
  datacenter?: DataCentre | null;
  place?: Place | null;
  analysis?: AreaAnalysis | null;
  media?: Media | null;
  profile?: Profile | null;
  project?: Geometry | null;
  project_name?: string | null;
  assess?: Assess | null;
  sites?: Site[] | null;
  sites_meta?: { area_ha_selected: number; cells_considered: number; cells_excluded: Record<string, number> } | null;
  plan?: Plan | null;
  plan_id?: string | null;
  plantations?: Plantation[] | null;
  plantation_check?: Plantation | null;
}

export type AgentEvent =
  | { type: "step"; id: string; tool: string; label: string; tech: string[]; args: Record<string, unknown> }
  | { type: "step_done"; id: string; ok: boolean; ms: number; summary: string | null }
  | { type: "state"; patch: Partial<Workspace> & Record<string, unknown> }
  | { type: "ui"; view: "map" | "plan" | "monitor" | "field"; focus?: "assess" | "sites" | "site" | "place" | "area" | "datacenter"; site_id?: string; layer?: LayerId; plantation_id?: string; expect_alerts?: number }
  | { type: "outline"; steps: string[] }
  | { type: "region"; region: RegionInfo }
  | { type: "sources"; items: PolicySource[] }
  | { type: "text"; text: string }
  | { type: "error"; message: string }
  | { type: "done" };

async function json<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, init);
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail ?? detail; } catch { /* not JSON */ }
    throw new Error(`${res.status}: ${detail}`);
  }
  return res.json() as Promise<T>;
}

export const mediaUrl = (u: string) => (u.startsWith("/") ? API_BASE + u : u);

/** Download through the API with a friendly file name (cross-origin <a download> is ignored by browsers). */
export async function downloadMedia(m: Media) {
  const name = `reroot-${m.title.toLowerCase().replace(/[^a-z0-9]+/g, "-")}-${m.years[0]}-${m.years[1]}.gif`;
  const res = await fetch(`${mediaUrl(m.url)}?download=1&filename=${encodeURIComponent(name)}`);
  const blob = await res.blob();
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 5000);
}

export const api = {
  regions: () => json<RegionInfo[]>("/v1/regions"),
  layer: (layer: LayerId) => json<{ tile_url: string }>(`/v1/layers/${layer}`),
  plantations: () => json<Plantation[]>("/v1/plantations"),
  timeseries: (id: string) => json<{ date: string; ndvi: number | null; expected: number | null }[]>(`/v1/plantations/${id}/timeseries`),
  alerts: (plantationId?: string) => json<Alert[]>(`/v1/alerts${plantationId ? `?plantation_id=${plantationId}` : ""}`),
  audioUrl: (alertId: string) => `${API_BASE}/v1/alerts/${alertId}/audio`,
  verifyPhoto: (plantationId: string, file: File) => {
    const fd = new FormData();
    fd.append("plantation_id", plantationId);
    fd.append("photo", file);
    return json<Verification>("/v1/verify-photo", { method: "POST", body: fd });
  },
};

/** Stream one agent turn. Calls onEvent for each Server-Sent Event. */
export async function chat(
  message: string,
  history: { role: "user" | "assistant"; text: string }[],
  workspace: Workspace,
  onEvent: (e: AgentEvent) => void,
  signal?: AbortSignal,
) {
  const res = await fetch(`${API_BASE}/v1/agent/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message, history, workspace }),
    signal,
  });
  if (!res.ok || !res.body) throw new Error(`${res.status}: ${res.statusText}`);
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let i;
    while ((i = buf.indexOf("\n\n")) >= 0) {
      const chunk = buf.slice(0, i);
      buf = buf.slice(i + 2);
      const line = chunk.split("\n").find((l) => l.startsWith("data: "));
      if (line) onEvent(JSON.parse(line.slice(6)) as AgentEvent);
    }
  }
}
