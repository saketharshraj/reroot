import { useEffect, useRef, useState } from "react";
import { api, type Alert, type Plantation, type Verification } from "./api";
import { watchAlerts } from "./firebase";
import { Icon } from "./ui";

/** The caretaker's side: alerts arrive as Hindi voice notes; they reply with a photo. */
export function FieldPhone({ plantations, plantationId, expectSince, onClose }: {
  plantations: Plantation[]; plantationId: string | null; expectSince: number | null; onClose: () => void;
}) {
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [waiting, setWaiting] = useState(false);
  const [english, setEnglish] = useState<Record<string, boolean>>({});
  const [checks, setChecks] = useState<{ v?: Verification; file: string; err?: string; at: number }[]>([]);
  const [target, setTarget] = useState<string | null>(plantationId);
  const file = useRef<HTMLInputElement>(null);
  const scroller = useRef<HTMLDivElement>(null);
  const pid = target ?? plantations[0]?.plantation_id ?? null;
  const plantation = plantations.find((p) => p.plantation_id === pid);

  useEffect(() => setTarget(plantationId ?? target), [plantationId]);

  // Alerts arrive live from Firestore (the Pub/Sub worker on Cloud Run writes each one there).
  // Without Firebase config, or if the listener fails, fall back to polling the API.
  const [live, setLive] = useState(false);
  useEffect(() => {
    const since = expectSince ?? 0;
    setWaiting(!!expectSince);
    if (!pid) return;
    const unsub = watchAlerts(pid, (a) => {
      setLive(true);
      setAlerts(a.slice(0, 6).reverse());
      if (a.some((x) => new Date(x.created_at).getTime() >= since - 60_000)) setWaiting(false);
    }, () => setLive(false));
    return () => { unsub?.(); };
  }, [pid, expectSince]);

  useEffect(() => {
    if (live) return;
    let stop = false;
    const since = expectSince ?? 0;
    const tick = async () => {
      try {
        const a = await api.alerts(pid ?? undefined);
        if (stop) return;
        setAlerts(a.slice(0, 6).reverse());
        if (a.some((x) => new Date(x.created_at).getTime() >= since - 60_000)) setWaiting(false);
      } catch { /* keep polling */ }
    };
    tick();
    const id = setInterval(tick, 4000);
    const quit = setTimeout(() => clearInterval(id), 120_000);
    return () => { stop = true; clearInterval(id); clearTimeout(quit); };
  }, [pid, expectSince, live]);

  useEffect(() => { scroller.current?.scrollTo({ top: 1e6, behavior: "smooth" }); }, [alerts, checks, waiting]);

  async function sendPhoto(f: File) {
    if (!pid) return;
    const at = Date.now();
    const url = URL.createObjectURL(f);
    setChecks((c) => [...c, { file: url, at }]);
    try {
      const v = await api.verifyPhoto(pid, f);
      setChecks((c) => c.map((x) => (x.at === at ? { ...x, v } : x)));
    } catch (e) {
      setChecks((c) => c.map((x) => (x.at === at ? { ...x, err: (e as Error).message } : x)));
    }
  }

  return (
    <aside className="phone" aria-label="Caretaker phone (demo)">
      <div className="phone-frame">
        <header className="phone-head">
          <div className="phone-avatar"><Icon name="leaf" size={14} /></div>
          <div>
            <b>ReRoot Field</b>
            <select value={pid ?? ""} onChange={(e) => setTarget(e.target.value)} aria-label="Plantation">
              {plantations.map((p) => <option key={p.plantation_id} value={p.plantation_id}>{p.caretaker_name}</option>)}
            </select>
          </div>
          <button className="x" onClick={onClose} aria-label="Close phone">×</button>
        </header>
        <div className="phone-body" ref={scroller}>
          <div className="phone-day">Caretaker's phone · {plantation?.name ?? ""}</div>
          {live && <div className="phone-live" title="Alerts stream from Firestore in real time"><i />Live · Firestore</div>}
          {alerts.map((a) => (
            <div key={a.alert_id} className={`pmsg in ${a.severity}`}>
              <div className="pmsg-title">{a.severity === "critical" ? "⚠️ " : ""}{a.title}</div>
              <p lang="hi">{a.message_local}</p>
              {a.has_audio && <audio controls preload="none" src={api.audioUrl(a.alert_id)} />}
              <button className="link" onClick={() => setEnglish((e) => ({ ...e, [a.alert_id]: !e[a.alert_id] }))}>
                {english[a.alert_id] ? "Hide English" : "Show English"}
              </button>
              {english[a.alert_id] && <p className="en">{a.message_en}</p>}
              <time>{new Date(a.created_at).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit" })}</time>
            </div>
          ))}
          {waiting && <div className="pmsg in typing"><span /><span /><span /></div>}
          {!alerts.length && !waiting && <p className="phone-empty">No alerts yet. Ask ReRoot to check the plantations.</p>}
          {checks.map((c) => (
            <div key={c.at}>
              <div className="pmsg out photo"><img src={c.file} alt="Photo sent by caretaker" /></div>
              {c.v ? (
                <div className={`pmsg in verify ${c.v.location.match === false || !c.v.is_plantation_photo ? "warning" : ""}`}>
                  <div className="pmsg-title">{c.v.is_plantation_photo ? "✅ Photo checked" : "⚠️ Not a plantation photo"}</div>
                  <p>
                    <b>{c.v.live_saplings_est}</b> live · <b>{c.v.dead_or_dry_est}</b> dry/dead · health {c.v.health}
                    <br />
                    {c.v.location.match == null ? "No GPS in photo" : c.v.location.match ? `Location matches (${c.v.location.distance_m} m from block)` : `Taken ${c.v.location.distance_m} m away from the block`}
                  </p>
                  <p className="en">{c.v.summary}</p>
                </div>
              ) : c.err ? <div className="pmsg in warning"><p>Couldn't check photo: {c.err}</p></div>
                : <div className="pmsg in typing"><span /><span /><span /></div>}
            </div>
          ))}
        </div>
        <footer className="phone-foot">
          <button className="btn primary block" onClick={() => file.current?.click()} disabled={!pid}><Icon name="camera" size={15} /> Send photo</button>
          <input ref={file} type="file" accept="image/*" capture="environment" hidden
            onChange={(e) => { const f = e.target.files?.[0]; if (f) sendPhoto(f); e.target.value = ""; }} />
        </footer>
      </div>
    </aside>
  );
}
