// Typed client for the ReRoot API. Contract: contracts/openapi.yaml.

export const API_BASE = (import.meta.env.VITE_API_BASE_URL as string | undefined)?.replace(/\/$/, "") ?? "http://localhost:8080";

export type Geometry = GeoJSON.Polygon | GeoJSON.MultiPolygon;

export type LayerId = "true_colour" | "ndvi" | "land_cover" | "similarity" | "rainfall" | "slope";

export interface LayerResponse {
  tile_url: string;
  legend: { min?: number; max?: number; palette?: string[] };
  sources: string[];
}

export interface Region {
  id: string;
  name: string;
  bbox: [number, number, number, number];
}

export interface AssessResponse {
  area_ha: number;
  tree_cover_ha: number;
  land_cover_ha: Record<string, number>;
  canopy_height_mean_m: number | null;
  agb_t: number | null;
  carbon_t_est: number | null;
  co2e_t_est: number | null;
  offset_required_ha: number;
  offset_required_ha_degraded: number;
  sources: string[];
}

export type Exclusion = "farmland" | "built" | "water" | "grassland" | "steep" | "protected";
export type WeightKey = "establishment" | "water" | "connectivity" | "people" | "terrain";

export interface SiteSearchRequest {
  project_geometry: Geometry;
  area_ha_needed: number;
  radius_km: number;
  exclude: Exclusion[];
  weights: Record<WeightKey, number>;
  region?: string;
}

export interface Site {
  site_id: string;
  rank: number;
  geometry: Geometry;
  centroid: [number, number];
  area_ha: number;
  final_score: number;
  breakdown: Record<WeightKey, number>;
  conditions: Record<string, number>;
  species: { name: string; local: string; why: string }[];
  rationale: string;
  sources: string[];
}

export interface SiteSearchResponse {
  run_id: string;
  sites: Site[];
  area_ha_selected: number;
  cells_considered: number;
  cells_excluded: Record<string, number>;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      detail = (await res.json()).detail ?? detail;
    } catch {
      /* not JSON */
    }
    throw new Error(`${res.status}: ${detail}`);
  }
  return res.json() as Promise<T>;
}

export const api = {
  regions: () => request<Region[]>("/v1/regions"),
  layer: (layer: LayerId, region: string) => request<LayerResponse>(`/v1/layers/${layer}?region=${region}`),
  assess: (geometry: Geometry) => request<AssessResponse>("/v1/assess", { method: "POST", body: JSON.stringify({ geometry }) }),
  searchSites: (body: SiteSearchRequest) =>
    request<SiteSearchResponse>("/v1/sites/search", { method: "POST", body: JSON.stringify(body) }),
};
