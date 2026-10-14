import { useState } from "react";
import { API_BASE, type Plan } from "./api";
import { fmt } from "./geo";
import { Icon } from "./ui";

export function PlanDoc({ plan, onClose }: { plan: Plan; onClose: () => void }) {
  const [rate, setRate] = useState<string>("");
  const cost = rate ? plan.totals.saplings * Number(rate) : null;
  const p = plan.project as Record<string, number | null>;
  return (
    <div className="plan-wrap">
      <div className="plan-toolbar no-print">
        <button className="btn ghost" onClick={onClose}><Icon name="map" size={15} /> Back to map</button>
        <span className="spacer" />
        <button className="btn primary" onClick={() => window.print()}><Icon name="print" size={15} /> Download PDF</button>
      </div>
      <article className="plan">
        <header className="plan-head">
          <div className="plan-brand"><Icon name="leaf" size={16} /> ReRoot</div>
          <h1>{plan.title}</h1>
          <p className="muted">
            {plan.region}{plan.prepared_for ? ` · Prepared for ${plan.prepared_for}` : ""} · {new Date(plan.generated_at).toLocaleDateString("en-IN", { day: "numeric", month: "long", year: "numeric" })} · Ref {plan.plan_id}
          </p>
        </header>

        <section>
          <h2>Summary</h2>
          <p className="lead">{plan.summary}</p>
          <div className="plan-kpis">
            <div><span>Tree cover lost</span><b>{fmt(p.tree_cover_ha, 1)} ha</b></div>
            <div><span>Carbon at risk</span><b>{fmt(p.co2e_t_est)} t CO₂e</b></div>
            <div><span>Planting required</span><b>{fmt(plan.requirement.offset_required_ha, 1)} ha</b></div>
            <div className="good"><span>Planting land found</span><b>{fmt(plan.totals.area_ha, 1)} ha</b></div>
            <div><span>Sites</span><b>{plan.totals.sites}</b></div>
            <div><span>Saplings</span><b>{fmt(plan.totals.saplings)}</b></div>
          </div>
        </section>

        {plan.map_image_url && (
          <figure>
            <img src={plan.map_image_url.startsWith("/") ? API_BASE + plan.map_image_url : plan.map_image_url} alt="Satellite map: project footprint in red, selected planting sites in green" />
            <figcaption><i className="key red" /> Project footprint <i className="key green" /> Selected planting sites · Sentinel-2, dry season 2025–26</figcaption>
          </figure>
        )}

        <section>
          <h2>Selected sites</h2>
          <table>
            <thead><tr><th>#</th><th>Area</th><th>Score</th><th>Survival odds</th><th>From project</th><th>Species</th></tr></thead>
            <tbody>
              {plan.sites.map((s) => (
                <tr key={s.rank}>
                  <td>{s.rank}</td><td>{fmt(s.area_ha, 0)} ha</td><td>{s.score}</td>
                  <td>top {Math.max(1, 100 - s.survival_rank_pct)}%</td><td>{fmt(s.distance_km, 1)} km</td><td>{s.species.join(", ")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>

        <section className="two">
          <div>
            <h2>Native species mix</h2>
            <ul className="plain">{plan.species_mix.map((s) => <li key={s.species}>{s.species} <span className="muted">· {s.sites} sites</span></li>)}</ul>
            <p className="muted small">Never plant Vilayati kikar (<i>Prosopis juliflora</i>), the invasive already spreading through the Aravallis.</p>
          </div>
          <div>
            <h2>Cost estimate</h2>
            <label className="rate">
              Rate per sapling (planting + 3 years' care)
              <span><em>₹</em><input inputMode="numeric" value={rate} placeholder="enter your rate" onChange={(e) => setRate(e.target.value.replace(/[^\d.]/g, ""))} /></span>
            </label>
            <p className="cost">{cost != null ? <>₹{fmt(cost)} <span className="muted">for {fmt(plan.totals.saplings)} saplings</span></> : <span className="muted">Enter your contracted rate to estimate the budget.</span>}</p>
          </div>
        </section>

        <section>
          <h2>Schedule</h2>
          <ol className="timeline">{plan.schedule.map((s) => <li key={s.when}><b>{s.when}</b><span>{s.what}</span></li>)}</ol>
        </section>

        <section>
          <h2>Monitoring and verification</h2>
          <ul className="plain">{plan.monitoring.map((m) => <li key={m}>{m}</li>)}</ul>
        </section>

        <section>
          <h2>Risks</h2>
          <table className="risks">
            <tbody>{plan.risks.map((r) => <tr key={r.risk}><th>{r.risk}</th><td>{r.mitigation}</td></tr>)}</tbody>
          </table>
        </section>

        <footer className="plan-foot">
          <h3>Assumptions</h3>
          <ul className="plain small">{plan.assumptions.map((a) => <li key={a}>{a}</li>)}</ul>
          <h3>Data sources</h3>
          <p className="small muted">{plan.sources.join(" · ")}</p>
        </footer>
      </article>
    </div>
  );
}
