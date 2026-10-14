import type { Assess, Site, Workspace } from "./api";
import { fmt } from "./geo";
import { Bar, Sources, Stat } from "./ui";

const LC: Record<string, [string, string]> = {
  trees: ["Trees", "#397d49"], shrub_and_scrub: ["Scrub", "#dfc35a"], grass: ["Grass", "#88b053"], crops: ["Crops", "#e49635"],
  built: ["Built-up", "#c4281b"], bare: ["Bare", "#a59b8f"], water: ["Water", "#419bdf"], flooded_vegetation: ["Wetland", "#7a87c6"],
};
const FACTORS: [string, string][] = [["establishment", "Survival odds"], ["water", "Water"], ["connectivity", "Reconnects forest"], ["people", "People nearby"], ["terrain", "Gentle terrain"]];
const EXCLUDED: Record<string, string> = {
  built: "built-up", farmland: "farmland", existing_forest: "already forest", water: "water", grassland: "grassland",
  steep: "steep", protected: "protected", project_footprint: "in the footprint",
};

function AssessCard({ a }: { a: Assess }) {
  const lc = Object.entries(a.land_cover_ha).filter(([, v]) => v > 0.1).sort((x, y) => y[1] - x[1]);
  const total = lc.reduce((s, [, v]) => s + v, 0) || 1;
  return (
    <section className="card">
      <header className="card-head"><span className="eyebrow red">Impact</span><h3>What this project clears</h3></header>
      <div className="stats">
        <Stat label="Footprint" value={fmt(a.area_ha, 1)} unit="ha" />
        <Stat label="Tree cover lost" value={fmt(a.tree_cover_ha, 1)} unit="ha" tone="bad" />
        <Stat label="Carbon at risk" value={fmt(a.co2e_t_est)} unit="t CO₂e" tone="bad" />
        <Stat label="Avg canopy" value={fmt(a.canopy_height_mean_m, 1)} unit="m" />
      </div>
      <div className="lcbar">{lc.map(([k, v]) => <i key={k} style={{ width: `${(v / total) * 100}%`, background: LC[k]?.[1] ?? "#999" }} title={`${LC[k]?.[0] ?? k} ${fmt(v, 1)} ha`} />)}</div>
      <ul className="lclegend">{lc.slice(0, 6).map(([k, v]) => <li key={k}><i style={{ background: LC[k]?.[1] }} />{LC[k]?.[0] ?? k} <b>{fmt(v, 1)} ha</b></li>)}</ul>
      <div className="callout">
        <span>Compensatory planting required</span>
        <b>{fmt(a.offset_required_ha, 1)} ha</b>
        <small>on non-forest land · {fmt(a.offset_required_ha_degraded, 1)} ha if on degraded forest land</small>
      </div>
      <Sources items={a.sources} />
    </section>
  );
}

function SitesCard({ sites, meta, need, selected, onSelect }: {
  sites: Site[]; meta: Workspace["sites_meta"]; need: number | null; selected: string | null; onSelect: (id: string) => void;
}) {
  const area = sites.reduce((s, x) => s + x.area_ha, 0);
  const excluded = Object.entries(meta?.cells_excluded ?? {}).sort((a, b) => b[1] - a[1]);
  return (
    <section className="card">
      <header className="card-head"><span className="eyebrow green">Where to plant</span><h3>{sites.length} sites · {fmt(area, 0)} ha{need ? ` of ${fmt(need, 0)} ha` : ""}</h3></header>
      {meta && (
        <p className="muted small">
          {fmt(meta.cells_considered)} one-hectare cells scored. Ruled out:{" "}
          {excluded.slice(0, 4).map(([k, v]) => `${EXCLUDED[k] ?? k} ${fmt(v)}`).join(" · ")}
        </p>
      )}
      <ol className="sitelist">
        {sites.map((s) => {
          const open = s.site_id === selected;
          return (
            <li key={s.site_id} className={open ? "open" : ""}>
              <button className="site-row" onClick={() => onSelect(s.site_id)} aria-expanded={open}>
                <span className="rank">{s.rank}</span>
                <span className="site-name">{fmt(s.area_ha, 0)} ha</span>
                <span className="site-meta">survival odds top {Math.max(1, 100 - Math.round((s.breakdown.establishment ?? 0) * 100))}%</span>
                <span className="score">{Math.round(s.final_score * 100)}</span>
              </button>
              {open && (
                <div className="site-detail">
                  <p>{s.rationale}</p>
                  {FACTORS.map(([k, l]) => <Bar key={k} label={l} value={s.breakdown[k] ?? 0} />)}
                  <div className="species">{s.species.map((sp) => <span key={sp.name} title={sp.why}><b>{sp.local}</b> <i>{sp.name}</i></span>)}</div>
                </div>
              )}
            </li>
          );
        })}
      </ol>
      <Sources items={sites[0]?.sources ?? []} />
    </section>
  );
}

export function ResultsPanel({ ws, selected, onSelect }: { ws: Workspace; selected: string | null; onSelect: (id: string) => void }) {
  if (!ws.assess && !ws.sites?.length) {
    return (
      <section className="card hint">
        <h3>Start with a footprint</h3>
        <p className="muted">Draw the road, mine or building on the map, upload a KML, or ask for the demo bypass. ReRoot will measure what it clears and find where to replant.</p>
      </section>
    );
  }
  return (
    <>
      {ws.sites?.length ? (
        <SitesCard sites={ws.sites} meta={ws.sites_meta} need={ws.assess?.offset_required_ha ?? null} selected={selected} onSelect={onSelect} />
      ) : null}
      {ws.assess && <AssessCard a={ws.assess} />}
    </>
  );
}
