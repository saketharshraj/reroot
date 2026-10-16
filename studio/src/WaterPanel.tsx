import type { WaterCheck, WaterLedger, WaterMonth, WaterProject, WaterSeason, WaterVerdict } from "./api";
import { fmt } from "./geo";
import { Icon, Sources } from "./ui";

export const VERDICT_LABEL: Record<WaterVerdict, string> = {
  verified: "Verified", partial: "Partial", at_risk: "At risk", not_additional: "Not additional",
  not_verified: "Not verified", too_early: "Too early", insufficient_data: "No baseline",
};
// Reuse the plantation status colours: green / amber / red / grey.
const TONE: Record<WaterVerdict, "on_track" | "watch" | "at_risk" | "unknown"> = {
  verified: "on_track", partial: "watch", at_risk: "at_risk", not_additional: "unknown",
  not_verified: "at_risk", too_early: "unknown", insufficient_data: "unknown",
};
export const VERDICT_COLOR: Record<WaterVerdict, string> = {
  verified: "#16a34a", partial: "#f59e0b", at_risk: "#dc2626", not_additional: "#64748b",
  not_verified: "#dc2626", too_early: "#94a3b8", insufficient_data: "#94a3b8",
};
const EYEBROW: Record<string, string> = { on_track: "green", watch: "amber", at_risk: "red", unknown: "" };

const m3 = (v: number | null | undefined) => (v == null ? "–" : `${fmt(v, 0)} m³`);

function WaterChart({ series, intervention, outlineHa }: { series: WaterMonth[]; intervention: string; outlineHa: number }) {
  if (!series.length) return <div className="chart-empty muted small">Loading satellite history…</div>;
  const W = 360, H = 170, L = 38, R = 6, T = 8, B = 30, RAIN_H = 22;
  const n = series.length;
  const x = (i: number) => L + (i / (n - 1 || 1)) * (W - L - R);
  const top = Math.max(outlineHa, ...series.map((s) => Math.max(s.water_ha ?? 0, s.optical_water_ha ?? 0, s.green_ha ?? 0))) * 1.05 || 1;
  const plotB = H - B - RAIN_H;
  const y = (v: number) => T + (1 - v / top) * (plotB - T);
  const path = (k: "water_ha" | "green_ha") => {
    let d = "", pen = false;
    series.forEach((s, i) => {
      const v = s[k];
      if (v == null) { pen = false; return; }
      d += `${pen ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`;
      pen = true;
    });
    return d;
  };
  const maxRain = Math.max(...series.map((s) => s.rain_mm ?? 0), 1);
  const iIdx = series.findIndex((s) => s.month >= intervention);
  const ticks = [0, top / 2, top].map((v) => Math.round(v));
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="chart" role="img" aria-label="Monthly water area from radar, with green cover and rainfall">
      {ticks.map((v) => (
        <g key={v}><line x1={L} x2={W - R} y1={y(v)} y2={y(v)} className="grid" /><text x={L - 4} y={y(v) + 3} textAnchor="end" className="axis">{v}{v === ticks[2] ? " ha" : ""}</text></g>
      ))}
      {series.map((s, i) => s.month.endsWith("-01") && (
        <text key={s.month} x={x(i) + 1} y={H - 4} className="axis">{s.month.slice(2, 4) === "18" ? "2018" : `’${s.month.slice(2, 4)}`}</text>
      ))}
      {series.map((s, i) => s.rain_mm ? (
        <rect key={`r${i}`} x={x(i) - 1} width={2.2} y={H - B - (s.rain_mm / maxRain) * RAIN_H + 4} height={(s.rain_mm / maxRain) * RAIN_H} className="rainbar" />
      ) : null)}
      {iIdx > 0 && (
        <g>
          <line x1={x(iIdx)} x2={x(iIdx)} y1={T} y2={H - B + 4} className="planted" />
          <text x={x(iIdx) + 3} y={T + 8} className="axis">works done</text>
        </g>
      )}
      <path d={path("green_ha")} className="green-line" />
      {series.map((s, i) => s.optical_water_ha != null && (
        <circle key={`o${i}`} cx={x(i)} cy={y(s.optical_water_ha)} r={1.6} className="optical-dot" />
      ))}
      <path d={path("water_ha")} className="water-line" />
    </svg>
  );
}

const MONTH_INITIAL = ["J", "F", "M", "A", "M", "J", "J", "A", "S", "O", "N", "D"];

/** The lake's own seasons: average rain per month, with the rainy, fullest and dry windows marked. */
function SeasonStrip({ season }: { season: WaterSeason }) {
  const max = Math.max(...season.climatology_mm, 1);
  return (
    <div className="season" aria-label={`Rains ${season.rain}, fullest ${season.peak}, dry season ${season.dry}`}>
      <div className="season-bars">
        {season.climatology_mm.map((v, i) => {
          const m = i + 1;
          const role = season.weak ? "" : season.rain_months.includes(m) ? "rain" : "";
          return (
            <div key={i} className="sb" title={`${Math.round(v)} mm`}>
              <i className={`sb-bar ${role}`} style={{ height: `${Math.max(4, (v / max) * 100)}%` }} />
              <i className={`sb-peak ${season.peak_months.includes(m) && !season.weak ? "on" : ""}`} />
              <i className={`sb-dry ${season.dry_months.includes(m) ? "on" : ""}`} />
              <span>{MONTH_INITIAL[i]}</span>
            </div>
          );
        })}
      </div>
      <p className="muted small">
        {season.weak
          ? <>No clear rainy season here, so whole calendar years are compared; dry season = {season.dry}.</>
          : <>Seasons from this lake's own rainfall: <b className="k-rain">rains {season.rain}</b>, <b className="k-peak">fullest {season.peak}</b>, <b className="k-dry">dry {season.dry}</b>.</>}
      </p>
    </div>
  );
}

export function LedgerCard({ ledger, onAsk }: { ledger: WaterLedger | null; onAsk: (t: string) => void }) {
  if (!ledger) return (
    <section className="card hint">
      <h3>Replenishment ledger</h3>
      <p className="muted small">Loading claimed vs satellite-verified water…</p>
    </section>
  );
  const cp = ledger.claimed_pct_of_target ?? 0, vp = ledger.verified_pct_of_target ?? 0;
  const scale = Math.max(100, cp, vp) * 1.08;
  return (
    <section className="card">
      <header className="card-head">
        <span className="eyebrow blue">Replenishment ledger</span>
        <h3>{ledger.sponsor}</h3>
      </header>
      <div className="ledger-bars" aria-label={`Claimed ${cp}% and verified ${vp}% of target`}>
        <div className="lrow"><span>Claimed</span><i className="ltrack"><i className="lclaimed" style={{ width: `${(cp / scale) * 100}%` }} /><i className="lmark" style={{ left: `${(100 / scale) * 100}%` }} /></i><b>{cp}%</b></div>
        <div className="lrow"><span>Verified</span><i className="ltrack"><i className="lverified" style={{ width: `${(vp / scale) * 100}%` }} /><i className="lmark" style={{ left: `${(100 / scale) * 100}%` }} /></i><b>{vp}%</b></div>
        <small className="muted">of the target · line = 100%</small>
      </div>
      <div className="stats">
        <div className="stat"><div className="stat-label">Target</div><div className="stat-value">{fmt(ledger.target_m3_yr, 0)}<small> m³/yr</small></div></div>
        <div className="stat bad"><div className="stat-label">Unverified gap</div><div className="stat-value">{fmt(ledger.gap_m3_yr, 0)}<small> m³/yr</small></div></div>
      </div>
      {ledger.target_source && <p className="muted small">Target: {ledger.target_source}.</p>}
      <button className="btn ghost block" onClick={() => onAsk("How could we close the replenishment gap?")}>How do we close the gap?</button>
    </section>
  );
}

export function WaterPanel({ projects, ledger, selectedId, detail, onSelect, onAsk }: {
  projects: WaterProject[]; ledger: WaterLedger | null; selectedId: string | null; detail: WaterCheck | null;
  onSelect: (id: string) => void; onAsk: (text: string) => void;
}) {
  const ev = detail?.evaluation;
  const tone = ev ? TONE[ev.verdict] : "unknown";
  const postYears = ev ? ev.years.filter((h) => ev.post_years.includes(h.label) || ev.baseline_years.includes(h.label)) : [];
  return (
    <>
      <LedgerCard ledger={ledger} onAsk={onAsk} />

      <section className="card">
        <header className="card-head">
          <span className="eyebrow blue">Water projects</span>
          <h3>{projects.length} funded lakes and ponds</h3>
        </header>
        <ul className="plist">
          {projects.map((p) => (
            <li key={p.project_id}>
              <button className={p.project_id === selectedId ? "on" : ""} onClick={() => p.project_id && onSelect(p.project_id)}>
                <span className="dot" style={{ background: VERDICT_COLOR[p.verdict ?? "too_early"] }} />
                <span className="pname">{p.name}<small>{p.place} · works {p.intervention_date.slice(0, 4)} · claims {fmt(p.claimed_m3_yr, 0)} m³</small></span>
                <span className={`pill ${TONE[p.verdict ?? "too_early"]}`}>{VERDICT_LABEL[p.verdict ?? "too_early"]}</span>
              </button>
            </li>
          ))}
        </ul>
        {projects.some((p) => p.is_demo) && <p className="muted small demo-note">Demo portfolio: the sponsor and claimed volumes are illustrative. The lakes and all satellite readings are real.</p>}
      </section>

      {detail && ev && (
        <section className="card">
          <header className="card-head">
            <span className={`eyebrow ${EYEBROW[tone]}`}>{VERDICT_LABEL[ev.verdict]}</span>
            <h3>{detail.name}</h3>
          </header>
          {detail.intervention && <p className="muted small">{detail.intervention} · finished {ev.intervention}</p>}
          <p className="small">{ev.verdict_text}{ev.note ? ` ${ev.note}` : ""}</p>
          <WaterChart series={detail.series} intervention={ev.intervention} outlineHa={ev.outline_ha} />
          <div className="chart-key small">
            <i className="k water" /> Open water (radar) <i className="k optical" /> Optical water <i className="k green" /> Dense green cover <i className="k rain" /> Rain
          </div>
          {ev.season && <SeasonStrip season={ev.season} />}
          <table className="wtable small">
            <thead><tr><th>Year</th><th>Peak</th><th>Rain-expected</th><th>Dry season</th><th>Rain</th></tr></thead>
            <tbody>
              {postYears.map((h) => {
                const post = ev.post_years.includes(h.label);
                return (
                  <tr key={h.label} className={post ? "post" : "base"}>
                    <td>{h.label}{!post && <em> base</em>}</td>
                    <td className={h.peak_gain ? "gain" : ""}>{fmt(h.peak_ha, 1)}</td>
                    <td>{post && h.expected_peak_ha != null ? fmt(h.expected_peak_ha, 1) : "–"}</td>
                    <td className={h.dry_gain ? "gain" : ""}>{h.dry_ha != null ? fmt(h.dry_ha, 1) : "–"}</td>
                    <td>{h.season_rain_mm != null ? `${fmt(h.season_rain_mm, 0)}` : "–"}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          <p className="muted small">Hectares of open water. Peak = {ev.season?.peak ?? "Aug–Nov"}, dry season = {ev.season?.dry ?? "Mar–Jun"}, rain = {ev.season?.rain ?? "Jun–Sep"} (mm); years run {ev.season?.water_year ?? "Jul–Jun"}.</p>
          <div className="stats">
            <div className="stat"><div className="stat-label">Credited / claimed</div><div className="stat-value">{fmt(ev.credited_m3_yr, 0)}<small> / {m3(ev.claimed_m3_yr)}</small></div></div>
            <div className="stat"><div className="stat-label">Last 6 months</div><div className="stat-value">{fmt(ev.recent?.open_water_ha, 1)}<small> ha water · {fmt(ev.recent?.green_cover_ha, 1)} ha green</small></div></div>
          </div>
          {ev.radar_optical_agreement != null && (
            <p className="muted small">Radar and optical agree on water in {Math.round(ev.radar_optical_agreement * 100)}% of clear months{ev.radar_optical_agreement < 0.7 ? ": weeds or smooth ground are confusing the radar here" : ""}.</p>
          )}
          <div className="btn-row">
            <button className="btn ghost" onClick={() => onAsk(`Show a timelapse of ${detail.name}`)}><Icon name="film" size={14} /> Timelapse</button>
            <button className="btn ghost" onClick={() => onAsk(`Why is ${detail.name} rated ${VERDICT_LABEL[ev.verdict].toLowerCase()}?`)}>Why this verdict?</button>
          </div>
          {ev.assumptions && (
            <details className="assume small">
              <summary>Method and assumptions</summary>
              <ul>{ev.assumptions.map((a) => <li key={a}>{a}</li>)}</ul>
            </details>
          )}
          {detail.notes && <p className="muted small">{detail.notes}</p>}
          {!!detail.references?.length && (
            <p className="small refs">{detail.references.map((r, i) => <a key={r} href={r} target="_blank" rel="noreferrer">Report {i + 1}</a>)}</p>
          )}
          <Sources items={detail.sources} />
        </section>
      )}
    </>
  );
}
