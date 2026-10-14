import { useEffect, useMemo, useRef, useState } from "react";
import { api, chat, type AgentEvent, type Geometry, type LayerId, type Role, type Workspace } from "./api";
import { Chat, ROLES, type Message } from "./Chat";
import { FieldPhone } from "./FieldPhone";
import { areaHa, readFootprint } from "./geo";
import { MonitorPanel } from "./MonitorPanel";
import { PlanDoc } from "./PlanDoc";
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
const ROLE_LABEL: Record<Role, string> = {
  developer: "Project developer", forest_department: "Forest department", ngo: "NGO", csr: "CSR team", other: "Guest",
};
const LAB_URL = (import.meta.env.VITE_LAB_URL as string | undefined) ?? "";
const uid = () => Math.random().toString(36).slice(2, 10);

export default function App() {
  const [ws, setWs] = useState<Workspace>({});
  const [messages, setMessages] = useState<Message[]>([]);
  const [busy, setBusy] = useState(false);
  const abort = useRef<AbortController | null>(null);
  const [view, setView] = useState<View>("map");
  const [layer, setLayer] = useState<LayerId>("true_colour");
  const [tiles, setTiles] = useState<Partial<Record<LayerId, string>>>({});
  const [drawing, setDrawing] = useState(false);
  const [selectedSite, setSelectedSite] = useState<string | null>(null);
  const [selectedPlantation, setSelectedPlantation] = useState<string | null>(null);
  const [focus, setFocus] = useState<MapFocus | null>(null);
  const [phone, setPhone] = useState<{ open: boolean; since: number | null; plantation: string | null }>({ open: false, since: null, plantation: null });

  const focusOn = (kind: MapFocus["kind"], id?: string) => setFocus({ kind, id, nonce: Date.now() });

  useEffect(() => {
    if (tiles[layer]) return;
    api.layer(layer).then((r) => setTiles((t) => ({ ...t, [layer]: r.tile_url }))).catch(() => {});
  }, [layer]);

  // Monitoring needs the plantation list even when the agent hasn't loaded it yet.
  useEffect(() => {
    if ((view === "monitor" || phone.open) && !ws.plantations?.length) {
      api.plantations().then((ps) => {
        setWs((w) => ({ ...w, plantations: ps }));
        if (view === "monitor") focusOn("plantations");
      }).catch(() => {});
    }
  }, [view, phone.open]);

  function patchMessage(id: string, f: (m: Message) => Message) {
    setMessages((ms) => ms.map((m) => (m.id === id ? f(m) : m)));
  }

  function handle(aid: string, e: AgentEvent) {
    switch (e.type) {
      case "step":
        patchMessage(aid, (m) => ({ ...m, steps: [...m.steps, { id: e.id, tool: e.tool, label: e.label, tech: e.tech, state: "running" }] }));
        break;
      case "step_done":
        patchMessage(aid, (m) => ({ ...m, steps: m.steps.map((s) => (s.id === e.id ? { ...s, state: e.ok ? "ok" : "fail", ms: e.ms, summary: e.summary } : s)) }));
        break;
      case "state": {
        setWs((w) => ({ ...w, ...e.patch }));
        if (e.patch.sites?.length) setSelectedSite(e.patch.sites[0].site_id);
        if (e.patch.plantation_check) setSelectedPlantation(e.patch.plantation_check.plantation_id);
        break;
      }
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
    const uidUser = uid(), aid = uid();
    const history = messages.filter((m) => m.text).map((m) => ({ role: m.role, text: m.text }));
    setMessages((ms) => [...ms, { id: uidUser, role: "user", text, steps: [] }, { id: aid, role: "assistant", text: "", steps: [], pending: true }]);
    setBusy(true);
    setDrawing(false);
    const ctrl = new AbortController();
    abort.current = ctrl;
    const payload: Workspace = {
      profile: profileOverride ?? ws.profile, project: ws.project, project_name: ws.project_name,
      assess: ws.assess, sites: ws.sites, plan_id: ws.plan_id,
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

  function setFootprint(g: Geometry, name: string) {
    setWs((w) => ({ ...w, project: g, project_name: name, assess: null, sites: null, sites_meta: null, plan: null, plan_id: null }));
    setSelectedSite(null);
    setView("map");
    focusOn("project");
  }

  const suggestions = useMemo(() => {
    if (!messages.length) return [];
    if (ws.plan && view === "plan") return ["How are our existing plantations doing?", "Which site has the best survival odds, and why?"];
    if (ws.plantations?.length && view === "monitor") return ["Check the Pali scrubland block", "Alert caretakers wherever action is needed", "Which plantation is doing best?"];
    if (ws.sites?.length) return ["Write the compensatory planting plan", "Why is site 1 ranked first?", "Show land that looks like the Mangar Bani forest"];
    if (ws.assess) return ["Find planting sites, avoid farmland, prioritise survival", "Find sites within 10 km that reconnect forest"];
    if (ws.project) return ["What will this footprint clear?"];
    return ["Use the demo bypass", "How are our plantations doing?"];
  }, [messages.length, ws.project, ws.assess, ws.sites, ws.plan, ws.plantations, view]);

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
            <button key={id} className={view === id ? "on" : ""} disabled={id === "plan" && !ws.plan} onClick={() => setView(id)}>
              <Icon name={icon} size={15} />{label}
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
      />

      <main className="canvas">
        <StudioMap
          tileUrl={tiles[layer] ?? null}
          opacity={0.92}
          project={view === "monitor" ? null : ws.project ?? null}
          sites={view === "monitor" ? [] : ws.sites ?? []}
          selectedSiteId={selectedSite}
          plantations={ws.plantations ?? []}
          selectedPlantationId={selectedPlantation}
          showPlantations={view === "monitor"}
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
        {view === "plan" && ws.plan && <PlanDoc plan={ws.plan} onClose={() => setView("map")} />}

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
