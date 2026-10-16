import type { AreaAnalysis, Assess, DataCentre, Site, Workspace } from "./api";
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

const COOLING_LABEL: Record<string, string> = { evaporative: "Evaporative towers", hybrid: "Hybrid (dry below 25 °C)", closed_loop: "Closed loop" };
const FLOW_LABEL: Record<string, [string, string]> = {
  reclaimed_water: ["Water in", "#0284c7"], clean_power: ["Power in", "#ca8a04"], heat: ["Heat out", "#ea580c"], replenish: ["Replenish", "#16a34a"],
};
const ml = (m3: number) => fmt(m3 / 1000, 0);

function riskTone(label: unknown) {
  const t = String(label ?? "");
  return /extremely high|high/i.test(t) ? "bad" : /medium/i.test(t) ? "warn" : "ok";
}

function DataCentreCard({ d }: { d: DataCentre }) {
  const risk: [string, unknown][] = [
    ["Water stress", d.water_risk.baseline_water_stress], ["Depletion", d.water_risk.water_depletion],
    ["Groundwater decline", d.water_risk.groundwater_table_decline], ["Drought risk", d.water_risk.drought_risk],
  ];
  const max = Math.max(...Object.values(d.water_m3_per_yr));
  const c = d.chosen;
  const pondsCover = c.replenishment_covered_by_ponds;
  return (
    <section className="card">
      <header className="card-head"><span className="eyebrow violet">Data centre · water-positive check</span>
        <h3>{d.it_load_mw} MW near {d.name ?? "this site"}</h3></header>
      <div className="risk-grid">
        {risk.map(([k, v]) => <div key={k} className={`risk ${riskTone(v)}`}><span>{k}</span><b>{String(v ?? "–").replace(/\s*\(.*\)/, "")}</b></div>)}
      </div>
      <p className="muted small">
        Groundwater trend {d.water_risk.grace_groundwater_trend_cm_per_yr ?? "–"} cm/yr (GRACE) · {fmt(Number(d.water_risk.rain_mm_per_yr))} mm rain ·
        {" "}{fmt(d.climate.hours_above_25c)} h/yr above 25 °C
      </p>
      <h4 className="mini-h">Cooling water a year</h4>
      {Object.entries(d.water_m3_per_yr).map(([k, v]) => (
        <div key={k} className={`wbar ${k === d.cooling ? "on" : ""}`}>
          <span>{COOLING_LABEL[k] ?? k}</span>
          <span className="wbar-track"><i style={{ width: `${Math.max(2, (v / max) * 100)}%` }} /></span>
          <b>{ml(v)} ML</b>
        </div>
      ))}
      <div className="loop">
        <div><span>Total</span><b>{ml(c.total_m3)} ML</b></div>
        <div className="blue"><span>From treated wastewater</span><b>{ml(c.reclaimed_m3)} ML</b></div>
        <div><span>Freshwater</span><b>{ml(c.freshwater_m3)} ML</b></div>
        <div className="green"><span>Replenish (120%)</span><b>{ml(c.replenishment_target_m3)} ML</b></div>
      </div>
      <h4 className="mini-h">The loop</h4>
      <ul className="flows">
        {d.flows.map((f, i) => {
          const other = (typeof f.from === "string" ? f.to : f.from) as { name: string; distance_km: number };
          const [tag, color] = FLOW_LABEL[f.kind] ?? [f.kind, "#64748b"];
          return <li key={i}><em style={{ background: color }}>{tag}</em><b>{other.name}</b><span>{other.distance_km} km · {f.label}</span></li>;
        })}
        {!d.flows.some((f) => f.kind === "heat") && <li className="muted small">No greenhouses, cold storage or industrial estates within 5 km: plan co-located heat users (e.g. cold storage for farmers).</li>}
      </ul>
      <p className="muted small">
        {pondsCover && c.ponds_to_rejuvenate.length
          ? `Rejuvenating ${c.ponds_to_rejuvenate.map((p) => `${p.name} (${p.area_ha} ha)`).join(", ")} could cover the replenishment target.`
          : `${c.ponds_nearby_count} ponds nearby; ponds alone don't cover the target.`}
        {c.plantable_ha_nearby != null && ` ${fmt(c.plantable_ha_nearby)} ha of plantable land within 10 km for watershed restoration.`}
      </p>
      <details className="assume"><summary>Assumptions</summary><ul>{d.assumptions.map((a) => <li key={a}>{a}</li>)}</ul></details>
      <Sources items={d.sources} />
    </section>
  );
}

function AreaCard({ a }: { a: AreaAnalysis }) {
  const lc = Object.entries(a.land_cover_pct).filter(([, v]) => v >= 0.5);
  return (
    <section className="card">
      <header className="card-head"><span className="eyebrow blue">Area analysis</span><h3>{a.radius_km} km around {a.name ?? "this point"}</h3></header>
      <div className="stats">
        <Stat label="Tree cover" value={fmt(a.tree_cover_ha, 0)} unit="ha" />
        <Stat label="Carbon stock" value={fmt(a.co2e_t_est)} unit="t CO₂e" />
        <Stat label="Plantable land" value={fmt(a.plantable_ha)} unit="ha" tone="good" />
        <Stat label="Top-25% survival odds" value={fmt(a.plantable_top_quartile_survival_ha)} unit="ha" tone="good" />
      </div>
      <div className="lcbar">{lc.map(([k, v]) => <i key={k} style={{ width: `${v}%`, background: LC[k]?.[1] ?? "#999" }} title={`${LC[k]?.[0] ?? k} ${v}%`} />)}</div>
      <ul className="lclegend">{lc.slice(0, 6).map(([k, v]) => <li key={k}><i style={{ background: LC[k]?.[1] }} />{LC[k]?.[0] ?? k} <b>{v}%</b></li>)}</ul>
      <Sources items={a.sources} />
    </section>
  );
}

export function ResultsPanel({ ws, selected, onSelect }: { ws: Workspace; selected: string | null; onSelect: (id: string) => void }) {
  const area = (
    <>
      {ws.datacenter ? <DataCentreCard d={ws.datacenter} /> : null}
      {ws.analysis ? <AreaCard a={ws.analysis} /> : null}
    </>
  );
  const hasExtra = !!(ws.datacenter || ws.analysis);
  if (!ws.assess && !ws.sites?.length) {
    if (hasExtra) return area;
    return (
      <section className="card hint">
        <h3>Start with a footprint</h3>
        <p className="muted">Draw the road, mine or building on the map, upload a KML, or ask for the demo bypass. ReRoot will measure what it clears and find where to replant.</p>
      </section>
    );
  }
  return (
    <>
      {area}
      {ws.sites?.length ? (
        <SitesCard sites={ws.sites} meta={ws.sites_meta} need={ws.assess?.offset_required_ha ?? null} selected={selected} onSelect={onSelect} />
      ) : null}
      {ws.assess && <AssessCard a={ws.assess} />}
    </>
  );
}
