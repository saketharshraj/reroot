import { useEffect, useState } from "react";
import { api, API_BASE, type AssessResponse, type Geometry, type LayerId, type Region, type SiteSearchResponse } from "./api";
import { AssessPanel } from "./AssessPanel";
import { LAYERS } from "./layers";
import { MapView } from "./MapView";
import { MonitorPanel } from "./MonitorPanel";
import { SitesPanel, type SiteQuery } from "./SitesPanel";
import demoProject from "./fixtures/demo_project.json";

type Tab = "assess" | "sites" | "monitor";

const DEFAULT_QUERY: SiteQuery = {
  area: 50,
  radiusKm: 15,
  exclude: ["farmland", "built", "water", "grassland", "steep"],
  weights: { establishment: 4, water: 3, connectivity: 3, people: 2, terrain: 2 },
};

export default function App() {
  const [tab, setTab] = useState<Tab>("assess");
  const [region, setRegion] = useState<Region | null>(null);
  const [layer, setLayer] = useState<LayerId>("true_colour");
  const [tiles, setTiles] = useState<Partial<Record<LayerId, string>>>({});
  const [opacity, setOpacity] = useState(0.9);
  const [project, setProject] = useState<Geometry | null>(null);
  const [drawing, setDrawing] = useState(false);
  const [assess, setAssess] = useState<AssessResponse | null>(null);
  const [assessing, setAssessing] = useState(false);
  const [query, setQuery] = useState<SiteQuery>(DEFAULT_QUERY);
  const [sites, setSites] = useState<SiteSearchResponse | null>(null);
  const [searching, setSearching] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.regions().then((r) => setRegion(r[0])).catch((e) => setError(`Can't reach the API at ${API_BASE} (${e.message})`));
  }, []);

  useEffect(() => {
    if (!region || tiles[layer]) return;
    api.layer(layer, region.id).then((r) => setTiles((t) => ({ ...t, [layer]: r.tile_url }))).catch((e) => setError(e.message));
  }, [layer, region]);

  async function runAssess(g: Geometry) {
    setProject(g);
    setSites(null);
    setSelected(null);
    setAssess(null);
    setAssessing(true);
    setError(null);
    try {
      const r = await api.assess(g);
      setAssess(r);
      setQuery((q) => ({ ...q, area: Math.max(1, Math.ceil(r.offset_required_ha)) }));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setAssessing(false);
    }
  }

  async function runSearch() {
    if (!project || !region) return;
    setSearching(true);
    setSelected(null);
    setError(null);
    try {
      const r = await api.searchSites({
        project_geometry: project, area_ha_needed: query.area, radius_km: query.radiusKm,
        exclude: query.exclude, weights: query.weights, region: region.id,
      });
      setSites(r);
      if (r.sites[0]) setSelected(r.sites[0].site_id);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSearching(false);
    }
  }

  const active = LAYERS.find((l) => l.id === layer)!;

  return (
    <div className="app">
      <header className="top">
        <div className="brand">
          <img src="/favicon.svg" alt="" width={28} height={28} />
          <div>
            <div className="brand-name">ReRoot</div>
            <div className="brand-tag">Plant where trees will survive. Then prove they did.</div>
          </div>
        </div>
        {region && <span className="region-chip">{region.name}</span>}
      </header>

      <aside className="side">
        <nav className="steps">
          {([
            ["assess", "1", "Assess"],
            ["sites", "2", "Find sites"],
            ["monitor", "3", "Monitor"],
          ] as const).map(([id, n, label]) => (
            <button key={id} className={`step ${tab === id ? "on" : ""}`} onClick={() => setTab(id)}>
              <span className="step-n">{n}</span>
              {label}
            </button>
          ))}
        </nav>

        {error && (
          <div className="error" role="alert">
            {error}
            <button className="x" onClick={() => setError(null)} aria-label="Dismiss">×</button>
          </div>
        )}

        {tab === "assess" && (
          <AssessPanel
            hasProject={!!project}
            drawing={drawing}
            loading={assessing}
            result={assess}
            onDraw={() => setDrawing((d) => !d)}
            onLoadDemo={() => runAssess(demoProject as Geometry)}
            onClear={() => {
              setProject(null);
              setAssess(null);
              setSites(null);
              setSelected(null);
            }}
            onNext={() => setTab("sites")}
          />
        )}
        {tab === "sites" && (
          <SitesPanel
            hasProject={!!project}
            query={query}
            setQuery={setQuery}
            loading={searching}
            result={sites}
            selectedSiteId={selected}
            onSelect={setSelected}
            onSearch={runSearch}
          />
        )}
        {tab === "monitor" && <MonitorPanel />}
      </aside>

      <main className="stage">
        <MapView
          bbox={region?.bbox ?? null}
          tileUrl={tiles[layer] ?? null}
          opacity={opacity}
          project={project}
          sites={sites?.sites ?? []}
          selectedSiteId={selected}
          drawing={drawing}
          onDrawFinish={(g) => {
            setDrawing(false);
            setTab("assess");
            runAssess(g);
          }}
          onSiteClick={(id) => {
            setTab("sites");
            setSelected(id);
          }}
        />
        <div className="layer-card">
          <div className="layer-tabs" role="radiogroup" aria-label="Map layer">
            {LAYERS.map((l) => (
              <button key={l.id} role="radio" aria-checked={l.id === layer} className={l.id === layer ? "on" : ""} onClick={() => setLayer(l.id)}>
                {l.label}
              </button>
            ))}
          </div>
          <div className="layer-hint">{active.hint}</div>
          {active.legend && (
            <ul className="legend-inline">
              {active.legend.map((x) => (
                <li key={x.label}><i style={{ background: x.color }} />{x.label}</li>
              ))}
            </ul>
          )}
          {active.ramp && (
            <div className="ramp">
              <span>{active.ramp.lo}</span>
              <i style={{ background: `linear-gradient(90deg, ${active.ramp.palette.map((c) => "#" + c).join(",")})` }} />
              <span>{active.ramp.hi}</span>
            </div>
          )}
          <label className="opacity">
            Opacity
            <input type="range" min={0} max={1} step={0.05} value={opacity} onChange={(e) => setOpacity(Number(e.target.value))} />
          </label>
        </div>
      </main>
    </div>
  );
}
