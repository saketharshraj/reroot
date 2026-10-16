import { useEffect, useRef } from "react";
import * as maplibregl from "maplibre-gl";
import type { GeoJSONSource, RasterTileSource } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
// MapLibre finds its worker relative to its own module URL, which breaks once Vite bundles it.
import workerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url";
import { TerraDraw, TerraDrawPolygonMode } from "terra-draw";
import { TerraDrawMapLibreGLAdapter } from "terra-draw-maplibre-gl-adapter";
import type { AreaAnalysis, DataCentre, Geometry, Place, Plantation, RegionInfo, Site } from "./api";

maplibregl.setWorkerUrl(workerUrl);

const BASEMAP = "https://tiles.openfreemap.org/styles/positron";
const EMPTY: GeoJSON.FeatureCollection = { type: "FeatureCollection", features: [] };
const fc = (features: GeoJSON.Feature[]): GeoJSON.FeatureCollection => ({ type: "FeatureCollection", features });

export interface MapFocus { kind: "project" | "sites" | "site" | "plantations" | "plantation" | "place" | "area" | "point" | "datacenter" | "region"; id?: string; lonlat?: [number, number]; nonce: number }

interface Props {
  tileUrl: string | null;
  opacity: number;
  project: Geometry | null;
  sites: Site[];
  selectedSiteId: string | null;
  plantations: Plantation[];
  selectedPlantationId: string | null;
  showPlantations: boolean;
  place: Place | null;
  analysis: AreaAnalysis | null;
  datacenter: DataCentre | null;
  regions: RegionInfo[];
  drawing: boolean;
  focus: MapFocus | null;
  onDrawFinish: (g: Geometry) => void;
  onSiteClick: (id: string) => void;
  onPlantationClick: (id: string) => void;
}

function bounds(geoms: Geometry[]) {
  const b = new maplibregl.LngLatBounds();
  let any = false;
  for (const g of geoms) {
    const polys = g.type === "Polygon" ? [g.coordinates] : g.coordinates;
    for (const p of polys) for (const [x, y] of p[0]) (b.extend([x, y]), (any = true));
  }
  return any ? b : null;
}

export function StudioMap(p: Props) {
  const el = useRef<HTMLDivElement>(null);
  const map = useRef<maplibregl.Map | null>(null);
  const draw = useRef<TerraDraw | null>(null);
  const ready = useRef(false);
  const cb = useRef(p);
  cb.current = p;

  useEffect(() => {
    const m = new maplibregl.Map({
      container: el.current!, style: BASEMAP, center: [77.2, 28.425], zoom: 10.8,
      attributionControl: { compact: true },
    });
    map.current = m;
    m.addControl(new maplibregl.NavigationControl({ showCompass: false }), "bottom-right");
    m.addControl(new maplibregl.ScaleControl({ unit: "metric" }), "bottom-left");

    m.on("load", () => {
      const firstSymbol = m.getStyle().layers.find((l) => l.type === "symbol")?.id;
      m.addSource("ee", { type: "raster", tiles: ["data:,"], tileSize: 256, attribution: "Google Earth Engine" });
      m.addLayer({ id: "ee", type: "raster", source: "ee", paint: { "raster-opacity": 0, "raster-fade-duration": 200 } }, firstSymbol);

      m.addSource("plantations", { type: "geojson", data: EMPTY });
      m.addLayer({
        id: "plantations-fill", type: "fill", source: "plantations",
        paint: {
          "fill-color": ["match", ["get", "status"], "on_track", "#16a34a", "watch", "#f59e0b", "at_risk", "#dc2626", "#94a3b8"],
          "fill-opacity": 0.55,
        },
      });
      m.addLayer({ id: "plantations-line", type: "line", source: "plantations", paint: { "line-color": "#ffffff", "line-width": 1.5 } });
      m.addLayer({
        id: "plantations-selected", type: "line", source: "plantations", filter: ["==", ["get", "id"], ""],
        paint: { "line-color": "#0f172a", "line-width": 3 },
      });
      m.addLayer({
        id: "plantations-label", type: "symbol", source: "plantations",
        layout: { "text-field": ["get", "label"], "text-size": 12, "text-font": ["Noto Sans Bold"], "text-offset": [0, 0], "text-allow-overlap": false },
        paint: { "text-color": "#0f172a", "text-halo-color": "#ffffff", "text-halo-width": 2 },
      });

      m.addSource("sites", { type: "geojson", data: EMPTY });
      m.addLayer({
        id: "sites-fill", type: "fill", source: "sites",
        paint: {
          "fill-color": ["interpolate", ["linear"], ["get", "score"], 0.55, "#bef264", 0.7, "#4ade80", 0.8, "#15803d"],
          "fill-opacity": 0.75,
        },
      });
      m.addLayer({ id: "sites-line", type: "line", source: "sites", paint: { "line-color": "#14532d", "line-width": 1.2 } });
      m.addLayer({
        id: "sites-selected", type: "line", source: "sites", filter: ["==", ["get", "id"], ""],
        paint: { "line-color": "#ffffff", "line-width": 4 },
      });
      m.addSource("site-labels", { type: "geojson", data: EMPTY });
      m.addLayer({
        id: "site-labels", type: "symbol", source: "site-labels",
        layout: { "text-field": ["get", "rank"], "text-size": 13, "text-font": ["Noto Sans Bold"], "text-allow-overlap": true },
        paint: { "text-color": "#ffffff", "text-halo-color": "#14532d", "text-halo-width": 2 },
      });

      m.addSource("regions", { type: "geojson", data: EMPTY });
      m.addLayer({
        id: "regions-line", type: "line", source: "regions",
        paint: { "line-color": ["match", ["get", "status"], "ready", "#ffffff", "failed", "#dc2626", "#f59e0b"], "line-width": 1.5, "line-dasharray": [3, 2], "line-opacity": 0.9 },
      });

      m.addSource("dc-flows", { type: "geojson", data: EMPTY });
      m.addLayer({
        id: "dc-flows", type: "line", source: "dc-flows",
        paint: { "line-color": ["get", "color"], "line-width": 2.5, "line-dasharray": [2, 1.5], "line-opacity": 0.95 },
      });
      m.addLayer({
        id: "dc-flow-labels", type: "symbol", source: "dc-flows",
        layout: { "symbol-placement": "line-center", "text-field": ["get", "label"], "text-size": 11, "text-font": ["Noto Sans Bold"], "text-allow-overlap": false },
        paint: { "text-color": ["get", "color"], "text-halo-color": "#ffffff", "text-halo-width": 2 },
      });
      m.addSource("dc-partners", { type: "geojson", data: EMPTY });
      m.addLayer({
        id: "dc-partners", type: "circle", source: "dc-partners",
        paint: { "circle-radius": ["case", ["get", "in_flow"], 7, 4], "circle-color": ["get", "color"], "circle-stroke-color": "#ffffff", "circle-stroke-width": 1.5, "circle-opacity": ["case", ["get", "in_flow"], 1, 0.6] },
      });
      m.addLayer({
        id: "dc-partner-labels", type: "symbol", source: "dc-partners", filter: ["get", "in_flow"],
        layout: { "text-field": ["get", "name"], "text-size": 11.5, "text-font": ["Noto Sans Bold"], "text-offset": [0, 1.2], "text-anchor": "top" },
        paint: { "text-color": "#0f172a", "text-halo-color": "#ffffff", "text-halo-width": 2 },
      });
      m.addSource("dc-site", { type: "geojson", data: EMPTY });
      m.addLayer({ id: "dc-site-halo", type: "circle", source: "dc-site", paint: { "circle-radius": 18, "circle-color": "#7c3aed", "circle-opacity": 0.2 } });
      m.addLayer({ id: "dc-site", type: "circle", source: "dc-site", paint: { "circle-radius": 9, "circle-color": "#7c3aed", "circle-stroke-color": "#ffffff", "circle-stroke-width": 2.5 } });
      m.addLayer({
        id: "dc-site-label", type: "symbol", source: "dc-site",
        layout: { "text-field": ["get", "name"], "text-size": 13, "text-font": ["Noto Sans Bold"], "text-offset": [0, 1.6], "text-anchor": "top" },
        paint: { "text-color": "#4c1d95", "text-halo-color": "#ffffff", "text-halo-width": 2 },
      });

      m.addSource("analysis", { type: "geojson", data: EMPTY });
      m.addLayer({ id: "analysis-fill", type: "fill", source: "analysis", paint: { "fill-color": "#2563eb", "fill-opacity": 0.08 } });
      m.addLayer({ id: "analysis-line", type: "line", source: "analysis", paint: { "line-color": "#1d4ed8", "line-width": 2, "line-dasharray": [2, 2] } });

      m.addSource("place", { type: "geojson", data: EMPTY });
      m.addLayer({ id: "place-halo", type: "circle", source: "place", paint: { "circle-radius": 14, "circle-color": "#2563eb", "circle-opacity": 0.2 } });
      m.addLayer({ id: "place-dot", type: "circle", source: "place", paint: { "circle-radius": 6, "circle-color": "#1d4ed8", "circle-stroke-color": "#ffffff", "circle-stroke-width": 2 } });
      m.addLayer({
        id: "place-label", type: "symbol", source: "place",
        layout: { "text-field": ["get", "name"], "text-size": 13, "text-font": ["Noto Sans Bold"], "text-offset": [0, 1.4], "text-anchor": "top" },
        paint: { "text-color": "#1e3a8a", "text-halo-color": "#ffffff", "text-halo-width": 2 },
      });

      m.addSource("project", { type: "geojson", data: EMPTY });
      m.addLayer({ id: "project-fill", type: "fill", source: "project", paint: { "fill-color": "#ef4444", "fill-opacity": 0.4 } });
      m.addLayer({ id: "project-line", type: "line", source: "project", paint: { "line-color": "#b91c1c", "line-width": 2 } });

      for (const [layer, handler] of [
        ["sites-fill", (id: string) => cb.current.onSiteClick(id)],
        ["plantations-fill", (id: string) => cb.current.onPlantationClick(id)],
      ] as const) {
        m.on("click", layer, (e) => {
          const id = e.features?.[0]?.properties?.id;
          if (id) handler(String(id));
        });
        m.on("mouseenter", layer, () => (m.getCanvas().style.cursor = "pointer"));
        m.on("mouseleave", layer, () => (m.getCanvas().style.cursor = ""));
      }

      const td = new TerraDraw({
        adapter: new TerraDrawMapLibreGLAdapter({ map: m }),
        modes: [new TerraDrawPolygonMode({ styles: { fillColor: "#ef4444", outlineColor: "#b91c1c", fillOpacity: 0.3, outlineWidth: 2, closingPointColor: "#ffffff" } })],
      });
      td.start();
      td.on("finish", (id) => {
        const f = td.getSnapshot().find((x) => x.id === id);
        if (f && f.geometry.type === "Polygon") cb.current.onDrawFinish(f.geometry as GeoJSON.Polygon);
        td.clear();
        td.setMode("static");
      });
      draw.current = td;
      ready.current = true;
      sync();
    });
    return () => m.remove();
  }, []);

  function sync() {
    const m = map.current;
    if (!m || !ready.current) return;
    const { tileUrl, opacity, project, sites, selectedSiteId, plantations, selectedPlantationId, showPlantations, place, analysis, datacenter, regions } = cb.current;
    (m.getSource("regions") as GeoJSONSource).setData(fc(regions.filter((r) => !r.built_in || r.status !== "ready").map((r) => {
      const [w, so, e, n] = r.bbox;
      return { type: "Feature", properties: { status: r.status }, geometry: { type: "Polygon", coordinates: [[[w, so], [e, so], [e, n], [w, n], [w, so]]] } };
    })));
    const dcColor: Record<string, string> = { reclaimed_water: "#0284c7", clean_power: "#ca8a04", heat: "#ea580c", heat_user: "#ea580c", replenish: "#16a34a", water_body: "#16a34a", data_centre: "#7c3aed" };
    const inFlow = new Set((datacenter?.flows ?? []).map((f) => (typeof f.from === "string" ? f.to : f.from) as { name: string }).map((x) => x.name));
    (m.getSource("dc-site") as GeoJSONSource).setData(datacenter ? fc([{ type: "Feature", properties: { name: `${datacenter.it_load_mw} MW data centre` }, geometry: { type: "Point", coordinates: datacenter.site } }]) : EMPTY);
    (m.getSource("dc-partners") as GeoJSONSource).setData(datacenter ? fc(datacenter.partners.map((x) => ({
      type: "Feature", properties: { name: x.name, color: dcColor[x.category] ?? "#64748b", in_flow: inFlow.has(x.name) }, geometry: { type: "Point", coordinates: [x.lon, x.lat] },
    }))) : EMPTY);
    (m.getSource("dc-flows") as GeoJSONSource).setData(datacenter ? fc(datacenter.flows.map((f) => {
      const other = (typeof f.from === "string" ? f.to : f.from) as { lon: number; lat: number };
      return { type: "Feature", properties: { label: f.label, color: dcColor[f.kind] ?? "#64748b" }, geometry: { type: "LineString", coordinates: [datacenter.site, [other.lon, other.lat]] } };
    })) : EMPTY);
    (m.getSource("analysis") as GeoJSONSource).setData(analysis ? fc([{ type: "Feature", properties: {}, geometry: analysis.geometry }]) : EMPTY);
    (m.getSource("place") as GeoJSONSource).setData(place ? fc([{
      type: "Feature", properties: { name: place.name }, geometry: { type: "Point", coordinates: [place.lon, place.lat] },
    }]) : EMPTY);
    if (tileUrl) (m.getSource("ee") as RasterTileSource).setTiles([tileUrl]);
    m.setPaintProperty("ee", "raster-opacity", tileUrl ? opacity : 0);
    (m.getSource("project") as GeoJSONSource).setData(project ? fc([{ type: "Feature", properties: {}, geometry: project }]) : EMPTY);
    (m.getSource("sites") as GeoJSONSource).setData(fc(sites.map((s) => ({
      type: "Feature", properties: { id: s.site_id, score: s.final_score }, geometry: s.geometry,
    }))));
    (m.getSource("site-labels") as GeoJSONSource).setData(fc(sites.map((s) => ({
      type: "Feature", properties: { rank: String(s.rank) }, geometry: { type: "Point", coordinates: s.centroid },
    }))));
    (m.getSource("plantations") as GeoJSONSource).setData(showPlantations ? fc(plantations.map((pl) => ({
      type: "Feature", properties: { id: pl.plantation_id, status: pl.status, label: pl.name }, geometry: pl.geometry,
    }))) : EMPTY);
    m.setFilter("sites-selected", ["==", ["get", "id"], selectedSiteId ?? ""]);
    m.setFilter("plantations-selected", ["==", ["get", "id"], selectedPlantationId ?? ""]);
  }

  useEffect(sync, [p.tileUrl, p.opacity, p.project, p.sites, p.selectedSiteId, p.plantations, p.selectedPlantationId, p.showPlantations, p.place, p.analysis, p.datacenter, p.regions]);

  useEffect(() => {
    draw.current?.setMode(p.drawing ? "polygon" : "static");
  }, [p.drawing]);

  useEffect(() => {
    const m = map.current;
    const f = p.focus;
    if (!m || !f) return;
    let geoms: Geometry[] = [];
    if (f.kind === "project" && p.project) geoms = [p.project];
    if (f.kind === "sites") geoms = [...(p.project ? [p.project] : []), ...p.sites.map((s) => s.geometry)];
    if (f.kind === "site") geoms = p.sites.filter((s) => s.site_id === f.id).map((s) => s.geometry);
    if (f.kind === "plantations") geoms = p.plantations.map((x) => x.geometry);
    if (f.kind === "plantation") geoms = p.plantations.filter((x) => x.plantation_id === f.id).map((x) => x.geometry);
    if (f.kind === "area" && p.analysis) geoms = [p.analysis.geometry];
    if (f.kind === "datacenter" && p.datacenter) {
      const pts: [number, number][] = [p.datacenter.site, ...p.datacenter.flows.map((fl) => {
        const o = (typeof fl.from === "string" ? fl.to : fl.from) as { lon: number; lat: number };
        return [o.lon, o.lat] as [number, number];
      })];
      const b = new maplibregl.LngLatBounds();
      pts.forEach((pt) => b.extend(pt));
      m.fitBounds(b, { padding: { top: 90, bottom: 60, left: 60, right: 420 }, maxZoom: 13, duration: 900 });
      return;
    }
    if (f.kind === "region") {
      const r = p.regions.find((x) => x.id === f.id);
      if (r) m.fitBounds([[r.bbox[0], r.bbox[1]], [r.bbox[2], r.bbox[3]]], { padding: { top: 60, bottom: 40, left: 40, right: 400 }, duration: 900 });
      return;
    }
    const point = f.kind === "point" ? f.lonlat : f.kind === "place" && p.place ? [p.place.lon, p.place.lat] as [number, number] : null;
    if (point) {
      m.flyTo({ center: point, zoom: 13.5, duration: 900, padding: { top: 0, bottom: 0, left: 0, right: 380 } });
      return;
    }
    const b = bounds(geoms);
    if (b) m.fitBounds(b, { padding: { top: 80, bottom: 60, left: 60, right: 420 }, maxZoom: f.kind === "site" || f.kind === "plantation" ? 15 : 13.5, duration: 900 });
  }, [p.focus?.nonce]);

  return <div ref={el} className="map" />;
}
