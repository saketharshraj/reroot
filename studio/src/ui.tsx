const PATHS: Record<string, string> = {
  leaf: "M11 20A7 7 0 0 1 9.8 6.1C15.5 5 17 4.48 19 2c1 2 2 4.18 2 8 0 5.5-4.78 10-10 10Z M2 21c0-3 1.85-5.36 5.08-6",
  check: "M20 6 9 17l-5-5",
  x: "M18 6 6 18M6 6l12 12",
  pen: "M12 20h9M16.5 3.5a2.1 2.1 0 1 1 3 3L7 19l-4 1 1-4Z",
  clip: "m21.4 11.1-9.2 9.2a6 6 0 0 1-8.5-8.5l9.2-9.2a4 4 0 0 1 5.7 5.7l-9.2 9.2a2 2 0 0 1-2.8-2.8l8.5-8.5",
  send: "m22 2-7 20-4-9-9-4Z M22 2 11 13",
  stop: "M6 6h12v12H6z",
  polygon: "M3 7l9-4 9 4-3 12H6Z",
  map: "M9 3 3 6v15l6-3 6 3 6-3V3l-6 3-6-3Z M9 3v15 M15 6v15",
  doc: "M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8Z M14 2v6h6 M8 13h8 M8 17h8",
  pulse: "M22 12h-4l-3 9L9 3l-3 9H2",
  phone: "M7 2h10a2 2 0 0 1 2 2v16a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2Z M11 18h2",
  camera: "M14.5 4h-5L7 7H4a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V9a2 2 0 0 0-2-2h-3Z M12 17a4 4 0 1 0 0-8 4 4 0 0 0 0 8Z",
  print: "M6 9V2h12v7 M6 18H4a2 2 0 0 1-2-2v-5a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2h-2 M6 14h12v8H6Z",
  layers: "m12 2 10 5-10 5L2 7Z M2 17l10 5 10-5 M2 12l10 5 10-5",
  drop: "M12 2.7s7 7.3 7 12.3a7 7 0 0 1-14 0c0-5 7-12.3 7-12.3Z",
  arrow: "M5 12h14M12 5l7 7-7 7",
  list: "M9 6h11 M9 12h11 M9 18h11 M4 6h.01 M4 12h.01 M4 18h.01",
  shield: "M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10Z",
  clock: "M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20Z M12 6v6l4 2",
  plus: "M12 5v14 M5 12h14",
  pin: "M12 22s7-7.5 7-13a7 7 0 0 0-14 0c0 5.5 7 13 7 13Z M12 11a2 2 0 1 0 0-4 2 2 0 0 0 0 4Z",
  film: "M4 4h16v16H4Z M4 9h16 M4 15h16 M9 4v16 M15 4v16",
};

export function Icon({ name, size = 16 }: { name: keyof typeof PATHS | string; size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}
      strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      {PATHS[name]?.split(" M").map((d, i) => <path key={i} d={i ? "M" + d : d} />)}
    </svg>
  );
}

const TECH_CLASS: Record<string, string> = {
  "Earth Engine": "t-ee", BigQuery: "t-bq", "Vertex AI": "t-vx", Gemini: "t-gm", "Pub/Sub": "t-ps", "Text-to-Speech": "t-tts",
  "Official sources": "t-gov", OpenStreetMap: "t-osm",
};

export function TechBadge({ name }: { name: string }) {
  return <span className={`tech ${TECH_CLASS[name] ?? ""}`}>{name}</span>;
}

export function Stat({ label, value, unit, tone }: { label: string; value: string; unit?: string; tone?: "bad" | "good" }) {
  return (
    <div className={`stat ${tone ?? ""}`}>
      <div className="stat-label">{label}</div>
      <div className="stat-value">{value}{unit && <small> {unit}</small>}</div>
    </div>
  );
}

export function Sources({ items }: { items: string[] }) {
  if (!items?.length) return null;
  return (
    <div className="sources">
      {items.map((s) => <code key={s} title={s}>{s.replace(/^projects\/[^/]+\/assets\//, "").replace(/^reroot-hack-2026\./, "")}</code>)}
    </div>
  );
}

export function Bar({ label, value }: { label: string; value: number }) {
  const pct = Math.round(Math.max(0, Math.min(1, value)) * 100);
  return (
    <div className="bar">
      <span>{label}</span>
      <span className="bar-track"><i style={{ width: `${pct}%` }} /></span>
      <b>{pct}</b>
    </div>
  );
}

export const STATUS_LABEL: Record<string, string> = { on_track: "On track", watch: "Watch", at_risk: "At risk", unknown: "No data" };
