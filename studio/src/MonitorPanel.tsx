import { useEffect, useState } from "react";
import { api, type Plantation } from "./api";
import { fmt } from "./geo";
import { Icon, Sources, STATUS_LABEL } from "./ui";

type Point = { date: string; ndvi: number | null; expected: number | null };

function Chart({ data, planted }: { data: Point[]; planted: string }) {
  if (!data.length) return <div className="chart-empty muted small">Loading satellite history…</div>;
  const W = 360, H = 150, L = 26, R = 6, T = 8, B = 18;
  const t = (d: string) => new Date(d).getTime();
  const t0 = t(data[0].date), t1 = t(data[data.length - 1].date);
  const x = (d: string) => L + ((t(d) - t0) / (t1 - t0 || 1)) * (W - L - R);
  const y = (v: number) => T + (1 - (v - 0.1) / 0.8) * (H - T - B);
  const line = (k: "ndvi" | "expected") =>
    data.filter((p) => p[k] != null).map((p, i) => `${i ? "L" : "M"}${x(p.date).toFixed(1)},${y(p[k]!).toFixed(1)}`).join(" ");
  const years = [...new Set(data.map((p) => p.date.slice(0, 4)))];
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="chart" role="img" aria-label="Monthly greenness compared with the expected trajectory">
      {[0.2, 0.4, 0.6, 0.8].map((v) => (
        <g key={v}><line x1={L} x2={W - R} y1={y(v)} y2={y(v)} className="grid" /><text x={L - 4} y={y(v) + 3} textAnchor="end" className="axis">{v}</text></g>
      ))}
      {years.map((yr) => <text key={yr} x={x(`${yr}-01-01`) + 2} y={H - 4} className="axis">{yr}</text>)}
      {t(planted) >= t0 && <line x1={x(planted)} x2={x(planted)} y1={T} y2={H - B} className="planted" />}
      <path d={line("expected")} className="expected" />
      <path d={line("ndvi")} className="actual" />
    </svg>
  );
}

export function MonitorPanel({ plantations, selectedId, check, onSelect, onAsk }: {
  plantations: Plantation[]; selectedId: string | null; check: Plantation | null;
  onSelect: (id: string) => void; onAsk: (text: string) => void;
}) {
  const sel = plantations.find((p) => p.plantation_id === selectedId) ?? null;
  const [series, setSeries] = useState<Point[]>([]);
  useEffect(() => {
    setSeries([]);
    if (sel) api.timeseries(sel.plantation_id).then(setSeries).catch(() => setSeries([]));
  }, [sel?.plantation_id]);
  const w = check?.plantation_id === sel?.plantation_id ? check?.weather : undefined;
  const counts = plantations.reduce<Record<string, number>>((a, p) => ((a[p.status] = (a[p.status] ?? 0) + 1), a), {});

  return (
    <>
      <section className="card">
        <header className="card-head">
          <span className="eyebrow green">Monitoring</span>
          <h3>{plantations.length} plantations · {fmt(plantations.reduce((s, p) => s + p.area_ha, 0), 0)} ha</h3>
        </header>
        <div className="status-strip">
          {(["on_track", "watch", "at_risk"] as const).map((s) => <span key={s} className={`pill ${s}`}>{counts[s] ?? 0} {STATUS_LABEL[s]}</span>)}
        </div>
        <ul className="plist">
          {plantations.map((p) => (
            <li key={p.plantation_id}>
              <button className={p.plantation_id === selectedId ? "on" : ""} onClick={() => onSelect(p.plantation_id)}>
                <span className={`dot ${p.status}`} />
                <span className="pname">{p.name}<small>{fmt(p.area_ha, 0)} ha · planted {new Date(p.planted_date).getFullYear()}</small></span>
                <span className={`pill ${p.status}`}>{STATUS_LABEL[p.status]}</span>
              </button>
            </li>
          ))}
        </ul>
        {plantations.some((p) => p.is_demo) && <p className="muted small demo-note">Demo records on real satellite history: names and dates are illustrative.</p>}
      </section>

      {sel && (
        <section className="card">
          <header className="card-head">
            <span className={`eyebrow ${sel.status === "on_track" ? "green" : sel.status === "watch" ? "amber" : "red"}`}>{STATUS_LABEL[sel.status]}</span>
            <h3>{sel.name}</h3>
          </header>
          <p className="muted small">{sel.source_project} · {fmt(sel.saplings_planted)} saplings · caretakers: {sel.caretaker_name}</p>
          <div className="index">
            <div><b>{sel.establishment_index != null ? Math.round(sel.establishment_index * 100) : "–"}%</b><span>of target greenness for its age</span></div>
          </div>
          <Chart data={series} planted={sel.planted_date} />
          <div className="chart-key small"><i className="k actual" /> Satellite greenness (NDVI) <i className="k expected" /> Expected for a healthy plantation <i className="k planted" /> Planted</div>
          {w ? (
            <div className="wx">
              <div><Icon name="drop" size={14} /><b>{fmt(w.precip_next_10d_mm, 0)} mm</b><span>rain forecast, next 10 days</span></div>
              <div><b>{fmt(w.precip_last_14d_mm, 0)} mm</b><span>rain, last 14 days</span></div>
              <div><b>{w.soil_moisture_vs_normal_pct != null ? `${w.soil_moisture_vs_normal_pct > 0 ? "+" : ""}${w.soil_moisture_vs_normal_pct}%` : "–"}</b><span>soil moisture vs normal</span></div>
            </div>
          ) : (
            <button className="btn ghost block" onClick={() => onAsk(`Check the weather risk for ${sel.name}`)}>Check live weather risk</button>
          )}
          <button className="btn primary block" onClick={() => onAsk(`Alert the caretakers of ${sel.name} if action is needed`)}>
            <Icon name="phone" size={15} /> Alert caretakers
          </button>
          <Sources items={[...sel.sources, ...(w?.sources ?? [])]} />
        </section>
      )}
    </>
  );
}
