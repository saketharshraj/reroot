import type { Exclusion, SiteSearchResponse, WeightKey } from "./api";
import { Bar, Sources } from "./ui";

export interface SiteQuery {
  area: number;
  radiusKm: number;
  exclude: Exclusion[];
  weights: Record<WeightKey, number>;
}

interface Props {
  hasProject: boolean;
  query: SiteQuery;
  setQuery: (q: SiteQuery) => void;
  loading: boolean;
  result: SiteSearchResponse | null;
  selectedSiteId: string | null;
  onSelect: (id: string) => void;
  onSearch: () => void;
}

const EXCLUSIONS: { id: Exclusion; label: string; why: string }[] = [
  { id: "farmland", label: "Farmland", why: "Don't take land from farmers" },
  { id: "built", label: "Built-up", why: "Roads, buildings, settlements" },
  { id: "water", label: "Water & wetland", why: "Wetlands are habitat already" },
  { id: "grassland", label: "Natural grassland", why: "Trees on grassland harm wildlife" },
  { id: "steep", label: "Steep slopes", why: "Over 20°, saplings wash out" },
  { id: "protected", label: "Protected areas", why: "Sanctuaries managed separately" },
];

const WEIGHTS: { id: WeightKey; label: string; why: string }[] = [
  { id: "establishment", label: "Survival odds", why: "ML model: where land actually turned into tree cover" },
  { id: "water", label: "Water", why: "Rainfall, soil moisture, nearby water" },
  { id: "connectivity", label: "Reconnect forest", why: "Close to existing tree patches" },
  { id: "people", label: "People benefit", why: "More people within 1 km" },
  { id: "terrain", label: "Gentle terrain", why: "Lower slope" },
];

const BREAKDOWN_LABELS: Record<WeightKey, string> = {
  establishment: "Survival", water: "Water", connectivity: "Connect", people: "People", terrain: "Terrain",
};

const fmt = (n: number, d = 0) => n.toLocaleString("en-IN", { maximumFractionDigits: d, minimumFractionDigits: d });

export function SitesPanel({ hasProject, query, setQuery, loading, result, selectedSiteId, onSelect, onSearch }: Props) {
  const toggle = (id: Exclusion) =>
    setQuery({ ...query, exclude: query.exclude.includes(id) ? query.exclude.filter((x) => x !== id) : [...query.exclude, id] });

  return (
    <section className="panel">
      {!hasProject && <p className="notice">Mark a project footprint in step 1 first. Sites are searched around it.</p>}

      <div className="field-row">
        <label className="field">
          <span>Area needed</span>
          <span className="input-wrap">
            <input type="number" min={1} step={1} value={query.area} onChange={(e) => setQuery({ ...query, area: Number(e.target.value) })} />
            <em>ha</em>
          </span>
        </label>
        <label className="field">
          <span>Within {query.radiusKm} km</span>
          <input type="range" min={5} max={30} step={1} value={query.radiusKm} onChange={(e) => setQuery({ ...query, radiusKm: Number(e.target.value) })} />
        </label>
      </div>

      <details className="fold" open>
        <summary>Never plant on</summary>
        <div className="toggles">
          {EXCLUSIONS.map((x) => (
            <label key={x.id} className={`toggle ${query.exclude.includes(x.id) ? "on" : ""}`} title={x.why}>
              <input type="checkbox" checked={query.exclude.includes(x.id)} onChange={() => toggle(x.id)} />
              {x.label}
            </label>
          ))}
        </div>
        <p className="muted small">Existing forest is always excluded — there is nothing to plant there.</p>
      </details>

      <details className="fold">
        <summary>What matters most</summary>
        {WEIGHTS.map((w) => (
          <label key={w.id} className="weight" title={w.why}>
            <span>{w.label}</span>
            <input
              type="range" min={0} max={5} step={1} value={query.weights[w.id]}
              onChange={(e) => setQuery({ ...query, weights: { ...query.weights, [w.id]: Number(e.target.value) } })}
            />
            <b>{query.weights[w.id]}</b>
          </label>
        ))}
      </details>

      <button className="btn btn-primary btn-block" onClick={onSearch} disabled={!hasProject || loading || query.area <= 0}>
        {loading ? "Scoring 1-ha cells in BigQuery…" : "Find planting sites"}
      </button>

      {result && !loading && (
        <>
          <div className="summary">
            <b>{result.sites.length} sites</b>, {fmt(result.area_ha_selected, 0)} ha of {fmt(query.area, 0)} ha needed
            <div className="muted small">
              {fmt(result.cells_considered)} one-hectare cells checked
              {Object.keys(result.cells_excluded).length > 0 &&
                " · excluded: " +
                  Object.entries(result.cells_excluded)
                    .sort((a, b) => b[1] - a[1])
                    .map(([k, v]) => `${k.replace("_", " ")} ${fmt(v)}`)
                    .join(", ")}
            </div>
          </div>
          <ol className="sites">
            {result.sites.map((s) => (
              <li key={s.site_id} className={`site ${s.site_id === selectedSiteId ? "selected" : ""}`} onClick={() => onSelect(s.site_id)}>
                <div className="site-head">
                  <span className="rank">{s.rank}</span>
                  <span className="site-area">{fmt(s.area_ha, 0)} ha</span>
                  <span className="score" title="Overall suitability">{Math.round(s.final_score * 100)}</span>
                </div>
                {s.site_id === selectedSiteId ? (
                  <div className="site-body">
                    {(Object.keys(BREAKDOWN_LABELS) as WeightKey[]).map((k) => (
                      <Bar key={k} label={BREAKDOWN_LABELS[k]} value={s.breakdown[k] ?? 0} />
                    ))}
                    <p className="rationale">{s.rationale}</p>
                    <div className="species">
                      {s.species.map((sp) => (
                        <span key={sp.name} className="sp" title={sp.why}>
                          <b>{sp.local}</b> <i>{sp.name}</i>
                        </span>
                      ))}
                    </div>
                    <Sources items={s.sources} />
                  </div>
                ) : (
                  <div className="mini-bars">
                    {(Object.keys(BREAKDOWN_LABELS) as WeightKey[]).map((k) => (
                      <span key={k} title={`${BREAKDOWN_LABELS[k]} ${Math.round((s.breakdown[k] ?? 0) * 100)}`}>
                        <i style={{ height: `${Math.max(8, (s.breakdown[k] ?? 0) * 100)}%` }} />
                      </span>
                    ))}
                  </div>
                )}
              </li>
            ))}
          </ol>
        </>
      )}
    </section>
  );
}
