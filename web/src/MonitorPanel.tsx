import series from "./fixtures/ndvi_mangar_bani.json";

// Real monthly NDVI for a 500 m patch at Mangar Bani (Sentinel-2, Cloud Score+ masked).
// Shows the seasonal curve monitoring compares each plantation against.
export function MonitorPanel() {
  const pts = series.filter((r) => r.ndvi != null) as { month: string; ndvi: number }[];
  const W = 340, H = 120, P = 6;
  const x = (i: number) => P + (i / (pts.length - 1)) * (W - 2 * P);
  const y = (v: number) => H - P - ((v - 0.2) / 0.65) * (H - 2 * P);
  const path = pts.map((r, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(r.ndvi).toFixed(1)}`).join(" ");
  const years = [...new Set(pts.map((r) => r.month.slice(0, 4)))];

  return (
    <section className="panel">
      <p className="lede">
        After planting, every site is checked monthly from orbit. When a site falls below its expected curve, or a dry
        spell is forecast while the soil is dry, the caretaker gets a voice note in their language.
      </p>
      <div className="chart-card">
        <div className="chart-title">
          Mangar Bani forest · monthly NDVI <span className="muted small">2023 – Sep 2026</span>
        </div>
        <svg viewBox={`0 0 ${W} ${H}`} className="chart" role="img" aria-label="NDVI rises every monsoon and falls in the dry season">
          {[0.3, 0.5, 0.7].map((v) => (
            <g key={v}>
              <line x1={P} x2={W - P} y1={y(v)} y2={y(v)} className="grid" />
              <text x={W - P} y={y(v) - 3} className="axis" textAnchor="end">{v}</text>
            </g>
          ))}
          {years.map((yr) => {
            const i = pts.findIndex((r) => r.month.startsWith(yr));
            return <text key={yr} x={x(i)} y={H - 1} className="axis">{yr}</text>;
          })}
          <path d={path} className="line" />
        </svg>
        <p className="muted small">Greens up every monsoon (Jul–Oct), dries out Mar–Jun. A healthy plantation follows the same rhythm, rising year on year.</p>
      </div>
      <div className="coming">
        <b>Next up in this tab</b>
        <ul>
          <li>Plantation list with survival status</li>
          <li>Dry-spell alerts → Hindi voice notes over Telegram</li>
          <li>Caretaker photo check with Gemini</li>
        </ul>
      </div>
    </section>
  );
}
