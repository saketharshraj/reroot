import { useEffect, useMemo, useRef, useState } from "react";
import { api, chat, type AgentEvent, type Geometry, type LayerId, type RegionInfo, type Role, type Workspace } from "./api";
import { Chat, ROLES, type Message } from "./Chat";
import { FieldPhone } from "./FieldPhone";
import { areaHa, readFootprint } from "./geo";
import * as hist from "./history";
import { MonitorPanel } from "./MonitorPanel";
import { MediaCard, PlanDoc, PlanEmpty } from "./PlanDoc";
import { ResultsPanel } from "./ResultsPanel";
import { StudioMap, type MapFocus } from "./StudioMap";
import { Icon } from "./ui";

type View = "map" | "plan" | "monitor";

const LAYERS: { id: LayerId; label: string }[] = [
  { id: "true_colour", label: "Satellite" },
  { id: "land_cover", label: "Land cover" },
  { id: "ndvi", label: "Vegetation" },
  { id: "similarity", label: "Like Mangar Bani" },
  { id: "rainfall", label: "Rainfall" },
  { id: "slope", label: "Slope" },
];
const LAYER_IDS = LAYERS.map((l) => l.id) as string[];
const ROLE_LABEL: Record<Role, string> = {
  developer: "Project developer", forest_department: "Forest department", ngo: "NGO", csr: "CSR team", other: "Guest",
};
const LAB_URL = (import.meta.env.VITE_LAB_URL as string | undefined) ?? "";
const uid = () => Math.random().toString(36).slice(2, 10);

function restore(): { id: string; messages: Message[]; ws: Workspace; view: View } {
  const id = hist.currentId();
  const c = id ? hist.loadAll().find((x) => x.id === id) : undefined;
  if (!c) return { id: uid(), messages: [], ws: {}, view: "map" };
  // Anything that was mid-flight when the tab closed is finished now.
  const messages = c.messages.map((m) => ({ ...m, pending: false, steps: m.steps.map((s) => (s.state === "running" ? { ...s, state: "fail" as const } : s)) }));
  return { id: c.id, messages, ws: c.workspace, view: (c.view as View) ?? "map" };
}

export default function App() {
  const initial = useMemo(restore, []);
  const [convId, setConvId] = useState(initial.id);
  const [history, setHistory] = useState(hist.loadAll);
  const [ws, setWs] = useState<Workspace>(initial.ws);
  const [messages, setMessages] = useState<Message[]>(initial.messages);
  const [busy, setBusy] = useState(false);
  const abort = useRef<AbortController | null>(null);
  const [view, setView] = useState<View>(initial.view);
  const [layer, setLayer] = useState<LayerId>("true_colour");
  const [tiles, setTiles] = useState<Record<string, string>>({});
  const [drawing, setDrawing] = useState(false);
  const [selectedSite, setSelectedSite] = useState<string | null>(initial.ws.sites?.[0]?.site_id ?? null);
  const [selectedPlantation, setSelectedPlantation] = useState<string | null>(null);
  const [focus, setFocus] = useState<MapFocus | null>(null);
  const [mediaOpen, setMediaOpen] = useState(false);
  const [phone, setPhone] = useState<{ open: boolean; since: number | null; plantation: string | null }>({ open: false, since: null, plantation: null });
  const [regions, setRegions] = useState<RegionInfo[]>([]);
  const prevRegions = useRef<Record<string, string>>({});

  const focusOn = (kind: MapFocus["kind"], id?: string, lonlat?: [number, number]) => setFocus({ kind, id, lonlat, nonce: Date.now() });

  useEffect(() => {
    hist.setCurrentId(convId);
  }, [convId]);

  // Save this conversation to the browser shortly after anything changes.
  useEffect(() => {
    if (!messages.length) return;
    const t = setTimeout(() => {
      hist.save({ id: convId, title: hist.titleFor(messages), updatedAt: Date.now(), messages, workspace: ws, view });
      setHistory(hist.loadAll());
    }, 600);
    return () => clearTimeout(t);
  }, [messages, ws, view, convId]);

  // Regions: poll while any is loading; announce in the chat when one becomes ready.
  const loading = regions.some((r) => !["ready", "failed"].includes(r.status));
  useEffect(() => {
    let stop = false;
    const tick = () => api.regions().then((rs) => {
      if (stop) return;
      for (const r of rs) {
        const before = prevRegions.current[r.id];
        if (before && before !== r.status && (r.status === "ready" || r.status === "failed")) {
          setMessages((ms) => [...ms, {
            id: uid(), role: "assistant", steps: [],
            text: r.status === "ready"
              ? `**${r.name} is ready.** Its 1-hectare grid is built and scored, so site search and planting potential now work there. Want me to find planting sites or analyse the area?`
              : `Loading **${r.name}** failed: ${r.message ?? "unknown error"}. You can ask me to try again.`,
          }]);
          if (r.status === "ready") focusOn("region", r.id);
        }
        prevRegions.current[r.id] = r.status;
      }
      setRegions(rs);
    }).catch(() => {});
    tick();
    const id = setInterval(tick, loading ? 6000 : 60000);
    return () => { stop = true; clearInterval(id); };
  }, [loading]);

  // Imagery follows the conversation: the loaded region around the current place, else a box around it.
  const focusPoint: [number, number] | null = ws.datacenter?.site ?? (ws.place ? [ws.place.lon, ws.place.lat] : null);
  const area = useMemo(() => {
    if (!focusPoint) return { key: "default", spec: {} as { region?: string; bbox?: number[] } };
    const [lon, lat] = focusPoint;
    const r = regions.find((x) => x.status === "ready" && lon >= x.bbox[0] && lon <= x.bbox[2] && lat >= x.bbox[1] && lat <= x.bbox[3]);
    if (r) return { key: r.id, spec: { region: r.id } };
    const d = 0.12;  // ~25 km box
    const bbox = [lon - d, lat - d, lon + d, lat + d];
    return { key: bbox.map((x) => x.toFixed(2)).join(","), spec: { bbox } };
  }, [focusPoint?.[0], focusPoint?.[1], regions]);
  const tileKey = `${layer}@${area.key}` as LayerId;

  useEffect(() => {
    if (tiles[tileKey]) return;
    api.layer(layer, area.spec).then((r) => setTiles((t) => ({ ...t, [tileKey]: r.tile_url }))).catch(() => {});
  }, [layer, area.key]);

  useEffect(() => {
    if ((view === "monitor" || phone.open) && !ws.plantations?.length) {
      api.plantations().then((ps) => {
        setWs((w) => ({ ...w, plantations: ps }));
        if (view === "monitor") focusOn("plantations");
      }).catch(() => {});
    }
  }, [view, phone.open]);

  useEffect(() => {
    if (ws.project) focusOn(ws.sites?.length ? "sites" : "project");
  }, []);

  function patchMessage(id: string, f: (m: Message) => Message) {
    setMessages((ms) => ms.map((m) => (m.id === id ? f(m) : m)));
  }

  function handle(aid: string, e: AgentEvent) {
    switch (e.type) {
      case "outline":
        patchMessage(aid, (m) => ({ ...m, outline: e.steps }));
        break;
      case "region":
        setRegions((rs) => [...rs.filter((r) => r.id !== e.region.id), e.region]);
        prevRegions.current[e.region.id] = e.region.status;
        setView("map");
        focusOn("region", e.region.id);
        break;
      case "sources":
        patchMessage(aid, (m) => ({ ...m, sources: e.items }));
        break;
      case "step":
        patchMessage(aid, (m) => ({ ...m, steps: [...m.steps, { id: e.id, tool: e.tool, label: e.label, tech: e.tech, state: "running" }] }));
        break;
      case "step_done":
        patchMessage(aid, (m) => ({ ...m, steps: m.steps.map((s) => (s.id === e.id ? { ...s, state: e.ok ? "ok" : "fail", ms: e.ms, summary: e.summary } : s)) }));
        break;
      case "state":
        setWs((w) => ({ ...w, ...e.patch }));
        if (e.patch.sites?.length) setSelectedSite(e.patch.sites[0].site_id);
        if (e.patch.plantation_check) setSelectedPlantation(e.patch.plantation_check.plantation_id);
        if (e.patch.media) setMediaOpen(true);
        break;
      case "ui":
        if (e.layer) setLayer(e.layer);
        if (e.view === "field") {
          setPhone({ open: true, since: e.expect_alerts ? Date.now() : null, plantation: e.plantation_id ?? null });
          break;
        }
        setView(e.view);
        if (e.view === "map") {
          if (e.focus === "sites") focusOn("sites");
          else if (e.focus === "site" && e.site_id) (setSelectedSite(e.site_id), focusOn("site", e.site_id));
          else if (e.focus === "place") focusOn("place");
          else if (e.focus === "area") focusOn("area");
          else if (e.focus === "datacenter") focusOn("datacenter");
          else if (e.focus === "assess" || !e.layer) focusOn("project");
        }
        if (e.view === "monitor") {
          if (e.plantation_id) (setSelectedPlantation(e.plantation_id), focusOn("plantation", e.plantation_id));
          else focusOn("plantations");
        }
        break;
      case "text":
        patchMessage(aid, (m) => ({ ...m, text: e.text }));
        break;
      case "error":
        patchMessage(aid, (m) => ({ ...m, error: e.message }));
        break;
      case "done":
        patchMessage(aid, (m) => ({ ...m, pending: false }));
        break;
    }
  }

  async function send(text: string, profileOverride?: Workspace["profile"]) {
    const aid = uid();
    const history = messages.filter((m) => m.text).map((m) => ({ role: m.role, text: m.text }));
    setMessages((ms) => [...ms, { id: uid(), role: "user", text, steps: [] }, { id: aid, role: "assistant", text: "", steps: [], pending: true }]);
    setBusy(true);
    setDrawing(false);
    const ctrl = new AbortController();
    abort.current = ctrl;
    const payload: Workspace = {
      profile: profileOverride ?? ws.profile, project: ws.project, project_name: ws.project_name,
      assess: ws.assess, sites: ws.sites, plan_id: ws.plan_id, place: ws.place, analysis: ws.analysis,
      media: ws.media, datacenter: ws.datacenter ? { ...ws.datacenter, partners: [] } : null,
    };
    try {
      await chat(text, history, payload, (e) => handle(aid, e), ctrl.signal);
    } catch (err) {
      if ((err as Error).name !== "AbortError") patchMessage(aid, (m) => ({ ...m, error: `Couldn't reach ReRoot (${(err as Error).message}).` }));
    } finally {
      patchMessage(aid, (m) => ({ ...m, pending: false }));
      setBusy(false);
    }
  }

  /** Links inside replies drive the canvas; unknown links are asked as a follow-up. */
  function onLink(href: string, text: string) {
    const [kind, ...rest] = href.split("-");
    const arg = rest.join("-");
    if (kind === "site") {
      const s = ws.sites?.find((x) => x.rank === Number(arg));
      if (s) return (setView("map"), setSelectedSite(s.site_id), focusOn("site", s.site_id));
    }
    if (kind === "plantation" && arg) return (setView("monitor"), setSelectedPlantation(arg), focusOn("plantation", arg));
    if (kind === "place" && arg) {
      const [lon, lat] = arg.split(",").map(Number);
      if (Number.isFinite(lon) && Number.isFinite(lat)) {
        setWs((w) => ({ ...w, place: { name: text || "Place", lon, lat } }));
        return (setView("map"), focusOn("point", undefined, [lon, lat]));
      }
    }
    if (kind === "layer" && LAYER_IDS.includes(arg)) return (setView("map"), setLayer(arg as LayerId));
    if (kind === "view" && ["map", "plan", "monitor"].includes(arg)) return setView(arg as View);
    if (href === "project" && ws.project) return (setView("map"), focusOn("project"));
    if (href === "media" && ws.media) return setMediaOpen(true);
    if (href === "area" && ws.analysis) return (setView("map"), focusOn("area"));
    if (href === "datacenter" && ws.datacenter) return (setView("map"), focusOn("datacenter"));
    if (text.trim() && !busy) send(text.trim());
  }

  function setFootprint(g: Geometry, name: string) {
    setWs((w) => ({ ...w, project: g, project_name: name, assess: null, sites: null, sites_meta: null, plan: null, plan_id: null }));
    setSelectedSite(null);
    setView("map");
    focusOn("project");
  }

  function openConversation(id: string) {
    const c = hist.loadAll().find((x) => x.id === id);
    if (!c) return;
    setConvId(c.id);
    setMessages(c.messages.map((m) => ({ ...m, pending: false })));
    setWs(c.workspace);
    setView((c.view as View) ?? "map");
    setSelectedSite(c.workspace.sites?.[0]?.site_id ?? null);
    setMediaOpen(false);
    if (c.workspace.project) focusOn(c.workspace.sites?.length ? "sites" : "project");
  }

  function newConversation() {
    abort.current?.abort();
    setConvId(uid());
    setMessages([]);
    setWs({});
    setView("map");
    setSelectedSite(null);
    setSelectedPlantation(null);
    setMediaOpen(false);
    setPhone({ open: false, since: null, plantation: null });
  }

  const suggestions = useMemo(() => {
    if (!messages.length) return [];
    if (ws.plan && view === "plan") return ["What do the rules say about planting on degraded forest land?", "How are our existing plantations doing?"];
    if (ws.plantations?.length && view === "monitor") return ["Check the Pali scrubland block", "Show a timelapse of the Bandhwari ridge restoration", "Alert caretakers wherever action is needed"];
    if (ws.sites?.length) return ["Write the compensatory planting plan", "Why is site 1 ranked first?", "Show a timelapse of site 1", "What do the rules say about planting on degraded forest land?"];
    if (ws.assess) return ["Find planting sites, avoid farmland, prioritise survival", "Find sites within 10 km that reconnect forest"];
    if (ws.datacenter) return ["What if we use hybrid cooling instead?", "What do the rules say about using treated wastewater?", "Show a closer timelapse of the site"];
    if (ws.media) return ["Make the timelapse closer and sharper", "Show it as vegetation health (NDVI)", "Show the post-monsoon season instead"];
    if (ws.analysis) return ["Show a timelapse of this area", "Find planting sites near here"];
    if (ws.project) return ["What will this footprint clear?"];
    return ["Use the demo bypass", "Screen a 50 MW data centre near Gwal Pahari for water risk", "What's the land like within 2 km of Mangar village?"];
  }, [messages.length, ws.project, ws.assess, ws.sites, ws.plan, ws.plantations, ws.analysis, view]);

  const role = ws.profile?.role ?? null;
  const footprint = ws.project ? { name: ws.project_name ?? "Footprint", area_ha: ws.assess?.area_ha ?? areaHa(ws.project) } : null;

  return (
    <div className="app">
      <header className="top">
        <div className="brand">
          <span className="logo"><Icon name="leaf" size={16} /></span>
          <b>ReRoot</b><span className="brand-sub">Studio</span>
        </div>
        <nav className="views" aria-label="Views">
          {([["map", "map", "Map"], ["plan", "doc", "Plan"], ["monitor", "pulse", "Monitor"]] as const).map(([id, icon, label]) => (
            <button key={id} className={view === id ? "on" : ""} onClick={() => setView(id)}>
              <Icon name={icon} size={15} />{label}
              {id === "plan" && ws.plan && <span className="dot-new" aria-label="plan ready" />}
            </button>
          ))}
        </nav>
        <div className="top-right">
          {role && <span className="chip"><Icon name="leaf" size={12} />{ws.profile?.organisation || ROLE_LABEL[role]}</span>}
          <button className={`chip button ${phone.open ? "on" : ""}`} onClick={() => setPhone((p) => ({ ...p, open: !p.open }))}>
            <Icon name="phone" size={13} />Field app
          </button>
          {LAB_URL && <a className="chip button" href={LAB_URL} target="_blank" rel="noreferrer"><Icon name="layers" size={13} />Data lab</a>}
        </div>
      </header>

      <Chat
        messages={messages}
        busy={busy}
        role={role}
        suggestions={suggestions}
        footprint={footprint}
        drawing={drawing}
        history={history}
        currentId={convId}
        onSend={(t) => send(t)}
        onPickRole={(r, prompt) => {
          const profile = { role: r, organisation: null };
          setWs((w) => ({ ...w, profile }));
          send(`${ROLES.find((x) => x.id === r)?.intro ?? ""} ${prompt}`.trim(), profile);
        }}
        onDraw={() => { setView("map"); setDrawing((d) => !d); }}
        onUpload={async (f) => {
          try { setFootprint(await readFootprint(f), f.name.replace(/\.[^.]+$/, "")); }
          catch (e) { alert((e as Error).message); }
        }}
        onClearFootprint={() => setWs((w) => ({ ...w, project: null, project_name: null, assess: null, sites: null, plan: null, plan_id: null }))}
        onStop={() => abort.current?.abort()}
        onLink={onLink}
        onNewChat={newConversation}
        onOpenChat={openConversation}
        onDeleteChat={(id) => { hist.remove(id); setHistory(hist.loadAll()); if (id === convId) newConversation(); }}
      />

      <main className="canvas">
        <StudioMap
          tileUrl={tiles[tileKey] ?? tiles[layer] ?? null}
          opacity={0.92}
          project={view === "monitor" ? null : ws.project ?? null}
          sites={view === "monitor" ? [] : ws.sites ?? []}
          selectedSiteId={selectedSite}
          plantations={ws.plantations ?? []}
          selectedPlantationId={selectedPlantation}
          showPlantations={view === "monitor"}
          place={ws.place ?? null}
          analysis={view === "monitor" ? null : ws.analysis ?? null}
          datacenter={view === "monitor" ? null : ws.datacenter ?? null}
          regions={regions}
          drawing={drawing}
          focus={focus}
          onDrawFinish={(g) => { setDrawing(false); setFootprint(g, "Drawn footprint"); }}
          onSiteClick={(id) => { setSelectedSite(id); setView("map"); }}
          onPlantationClick={(id) => { setSelectedPlantation(id); setView("monitor"); }}
        />

        {view !== "plan" && (
          <div className="layer-switch" role="radiogroup" aria-label="Map layer">
            {LAYERS.map((l) => (
              <button key={l.id} role="radio" aria-checked={layer === l.id} className={layer === l.id ? "on" : ""} onClick={() => setLayer(l.id)}>{l.label}</button>
            ))}
          </div>
        )}

        {view === "map" && (
          <div className="side">
            <ResultsPanel ws={ws} selected={selectedSite} onSelect={(id) => { setSelectedSite(id); focusOn("site", id); }} />
          </div>
        )}
        {view === "monitor" && (
          <div className="side">
            <MonitorPanel
              plantations={ws.plantations ?? []}
              selectedId={selectedPlantation}
              check={ws.plantation_check ?? null}
              onSelect={(id) => { setSelectedPlantation(id); focusOn("plantation", id); }}
              onAsk={(t) => send(t)}
            />
          </div>
        )}
        {view === "plan" && (ws.plan
          ? <PlanDoc plan={ws.plan} onClose={() => setView("map")} />
          : <PlanEmpty ws={ws} busy={busy} onAsk={(t) => send(t)} />)}

        {mediaOpen && ws.media && view !== "plan" && <MediaCard media={ws.media} onClose={() => setMediaOpen(false)} />}

        {regions.filter((r) => !["ready", "failed"].includes(r.status)).map((r) => (
          <div key={r.id} className="region-banner" role="status">
            <span className="spin" />
            <div>
              <b>Loading {r.name}</b>
              <span>{r.message ?? "Queued"}{r.eta_minutes ? ` · about ${r.eta_minutes} min in total` : ""}</span>
              <i className="rb-track"><i style={{ width: `${Math.round((r.progress ?? 0) * 100)}%` }} /></i>
            </div>
          </div>
        ))}

        {phone.open && (
          <FieldPhone
            plantations={ws.plantations ?? []}
            plantationId={phone.plantation ?? selectedPlantation}
            expectSince={phone.since}
            onClose={() => setPhone((p) => ({ ...p, open: false }))}
          />
        )}
      </main>
    </div>
  );
}
