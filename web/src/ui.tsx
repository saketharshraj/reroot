export function Stat({ label, value, unit, tone }: { label: string; value: string; unit?: string; tone?: "bad" | "good" }) {
  return (
    <div className={`stat ${tone ?? ""}`}>
      <div className="stat-label">{label}</div>
      <div className="stat-value">
        {value}
        {unit && <span className="stat-unit"> {unit}</span>}
      </div>
    </div>
  );
}

export function Sources({ items }: { items: string[] }) {
  if (!items.length) return null;
  return (
    <div className="sources">
      <span className="muted small">Data:</span>
      {items.map((s) => (
        <code key={s} className="chip" title={s}>
          {s.replace(/^projects\/[^/]+\/assets\//, "")}
        </code>
      ))}
    </div>
  );
}

export function Bar({ value, label }: { value: number; label: string }) {
  const pct = Math.round(Math.max(0, Math.min(1, value)) * 100);
  return (
    <div className="bar-row">
      <span className="bar-label">{label}</span>
      <span className="bar-track">
        <span className="bar-fill" style={{ width: `${pct}%` }} />
      </span>
      <span className="bar-val">{pct}</span>
    </div>
  );
}
