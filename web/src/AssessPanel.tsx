import type { AssessResponse } from "./api";
import { LC_COLORS, LC_LABELS } from "./layers";
import { Sources, Stat } from "./ui";

interface Props {
  hasProject: boolean;
  drawing: boolean;
  loading: boolean;
  result: AssessResponse | null;
  onDraw: () => void;
  onLoadDemo: () => void;
  onClear: () => void;
  onNext: () => void;
}

const fmt = (n: number | null | undefined, d = 0) =>
  n == null ? "–" : n.toLocaleString("en-IN", { maximumFractionDigits: d, minimumFractionDigits: d });

export function AssessPanel({ hasProject, drawing, loading, result, onDraw, onLoadDemo, onClear, onNext }: Props) {
  const lc = result ? Object.entries(result.land_cover_ha).filter(([, v]) => v > 0.05).sort((a, b) => b[1] - a[1]) : [];
  const lcTotal = lc.reduce((s, [, v]) => s + v, 0) || 1;

  return (
    <section className="panel">
      <p className="lede">
        Mark where a road, mine or building will clear land. ReRoot measures what will be lost and how much
        compensatory planting the law requires.
      </p>
      <div className="row">
        <button className={`btn ${drawing ? "btn-active" : ""}`} onClick={onDraw} disabled={loading}>
          {drawing ? "Click map to draw… double-click to finish" : "Draw footprint"}
        </button>
        <button className="btn btn-ghost" onClick={onLoadDemo} disabled={loading}>
          Load demo bypass
        </button>
        {hasProject && (
          <button className="btn btn-ghost" onClick={onClear} disabled={loading}>
            Clear
          </button>
        )}
      </div>

      {loading && <div className="loading">Measuring with Earth Engine…</div>}

      {result && !loading && (
        <>
          <div className="stats">
            <Stat label="Footprint" value={fmt(result.area_ha, 1)} unit="ha" />
            <Stat label="Tree cover lost" value={fmt(result.tree_cover_ha, 1)} unit="ha" tone="bad" />
            <Stat label="Avg canopy height" value={fmt(result.canopy_height_mean_m, 1)} unit="m" />
            <Stat label="Carbon at risk" value={fmt(result.co2e_t_est)} unit="t CO₂e" tone="bad" />
          </div>

          <h3>What's there today</h3>
          <div className="lcbar" role="img" aria-label="Land cover inside footprint">
            {lc.map(([k, v]) => (
              <span key={k} style={{ width: `${(v / lcTotal) * 100}%`, background: LC_COLORS[k] ?? "#888" }} title={`${LC_LABELS[k] ?? k}: ${fmt(v, 1)} ha`} />
            ))}
          </div>
          <ul className="legend-list">
            {lc.map(([k, v]) => (
              <li key={k}>
                <i style={{ background: LC_COLORS[k] ?? "#888" }} />
                {LC_LABELS[k] ?? k} <b>{fmt(v, 1)} ha</b>
              </li>
            ))}
          </ul>

          <div className="callout">
            <div className="callout-title">Compensatory planting needed</div>
            <div className="callout-big">{fmt(result.offset_required_ha, 1)} ha</div>
            <div className="muted small">
              on equivalent non-forest land, or {fmt(result.offset_required_ha_degraded, 1)} ha on degraded forest land
            </div>
          </div>

          <button className="btn btn-primary btn-block" onClick={onNext}>
            Find planting sites →
          </button>
          <Sources items={result.sources} />
        </>
      )}
    </section>
  );
}
