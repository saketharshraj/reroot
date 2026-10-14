import { useEffect, useRef } from "react";
import * as maplibregl from "maplibre-gl";
import type { GeoJSONSource, RasterTileSource } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
// MapLibre finds its worker relative to its own module URL, which breaks once Vite bundles it.
import workerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url";
import { TerraDraw, TerraDrawPolygonMode } from "terra-draw";
import { TerraDrawMapLibreGLAdapter } from "terra-draw-maplibre-gl-adapter";
import type { Geometry, Site } from "./api";

maplibregl.setWorkerUrl(workerUrl);

const BASEMAP = "https://tiles.openfreemap.org/styles/positron";
const EMPTY: GeoJSON.FeatureCollection = { type: "FeatureCollection", features: [] };

interface Props {
  bbox: [number, number, number, number] | null;
  tileUrl: string | null;
  opacity: number;
  project: Geometry | null;
  sites: Site[];
  selectedSiteId: string | null;
  drawing: boolean;
  onDrawFinish: (g: Geometry) => void;
  onSiteClick: (id: string) => void;
}

function bboxPolygon([w, s, e, n]: [number, number, number, number]): GeoJSON.Feature {
  return {
    type: "Feature",
    properties: {},
    geometry: { type: "Polygon", coordinates: [[[w, s], [e, s], [e, n], [w, n], [w, s]]] },
  };
}

function boundsOf(geoms: Geometry[]): maplibregl.LngLatBounds | null {
  const b = new maplibregl.LngLatBounds();
  let any = false;
  for (const g of geoms) {
    const rings = g.type === "Polygon" ? g.coordinates : g.coordinates.flat();
    for (const ring of rings) for (const [x, y] of ring) (b.extend([x, y]), (any = true));
  }
  return any ? b : null;
}

export function MapView(p: Props) {
  const el = useRef<HTMLDivElement>(null);
  const map = useRef<maplibregl.Map | null>(null);
  const draw = useRef<TerraDraw | null>(null);
  const ready = useRef(false);
  const cb = useRef(p);
  cb.current = p;

  // Create the map once.
  useEffect(() => {
    const m = new maplibregl.Map({
      container: el.current!,
      style: BASEMAP,
      center: [77.2, 28.425],
      zoom: 10.6,
      attributionControl: { compact: true },
    });
    map.current = m;
    m.addControl(new maplibregl.NavigationControl({ showCompass: false }), "bottom-right");
    m.addControl(new maplibregl.ScaleControl({ unit: "metric" }), "bottom-left");

    m.on("load", () => {
      const firstSymbol = m.getStyle().layers.find((l) => l.type === "symbol")?.id;
      m.addSource("ee", { type: "raster", tiles: ["data:,"], tileSize: 256, attribution: "Google Earth Engine" });
      m.addLayer({ id: "ee", type: "raster", source: "ee", paint: { "raster-opacity": 0 } }, firstSymbol);

      m.addSource("aoi", { type: "geojson", data: EMPTY });
      m.addLayer({ id: "aoi", type: "line", source: "aoi", paint: { "line-color": "#ffffff", "line-width": 1.5, "line-dasharray": [3, 2], "line-opacity": 0.8 } });

      m.addSource("sites", { type: "geojson", data: EMPTY, promoteId: "site_id" });
      m.addLayer({
        id: "sites-fill", type: "fill", source: "sites",
        paint: {
          "fill-color": ["interpolate", ["linear"], ["get", "final_score"], 0.4, "#fde047", 0.6, "#86efac", 0.8, "#16a34a"],
          "fill-opacity": 0.65,
        },
      });
      m.addLayer({ id: "sites-line", type: "line", source: "sites", paint: { "line-color": "#052e16", "line-width": 1.2 } });
      m.addLayer({
        id: "sites-selected", type: "line", source: "sites", filter: ["==", ["get", "site_id"], ""],
        paint: { "line-color": "#ffffff", "line-width": 3.5 },
      });
      m.addSource("site-labels", { type: "geojson", data: EMPTY });
      m.addLayer({
        id: "site-labels", type: "symbol", source: "site-labels",
        layout: { "text-field": ["get", "rank"], "text-size": 13, "text-font": ["Noto Sans Bold"], "text-allow-overlap": true },
        paint: { "text-color": "#ffffff", "text-halo-color": "#052e16", "text-halo-width": 2 },
      });

      m.addSource("project", { type: "geojson", data: EMPTY });
      m.addLayer({ id: "project-fill", type: "fill", source: "project", paint: { "fill-color": "#ef4444", "fill-opacity": 0.35 } });
      m.addLayer({ id: "project-line", type: "line", source: "project", paint: { "line-color": "#b91c1c", "line-width": 2 } });

      m.on("click", "sites-fill", (e) => {
        const id = e.features?.[0]?.properties?.site_id;
        if (id) cb.current.onSiteClick(String(id));
      });
      m.on("mouseenter", "sites-fill", () => (m.getCanvas().style.cursor = "pointer"));
      m.on("mouseleave", "sites-fill", () => (m.getCanvas().style.cursor = ""));

      const td = new TerraDraw({
        adapter: new TerraDrawMapLibreGLAdapter({ map: m }),
        modes: [
          new TerraDrawPolygonMode({
            styles: { fillColor: "#ef4444", outlineColor: "#b91c1c", fillOpacity: 0.3, outlineWidth: 2, closingPointColor: "#ffffff" },
          }),
        ],
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
    const { bbox, tileUrl, opacity, project, sites, selectedSiteId } = cb.current;
    (m.getSource("aoi") as GeoJSONSource).setData(bbox ? { type: "FeatureCollection", features: [bboxPolygon(bbox)] } : EMPTY);
    if (tileUrl) (m.getSource("ee") as RasterTileSource).setTiles([tileUrl]);
    m.setPaintProperty("ee", "raster-opacity", tileUrl ? opacity : 0);
    (m.getSource("project") as GeoJSONSource).setData(
      project ? { type: "FeatureCollection", features: [{ type: "Feature", properties: {}, geometry: project }] } : EMPTY,
    );
    (m.getSource("sites") as GeoJSONSource).setData({
      type: "FeatureCollection",
      features: sites.map((s) => ({ type: "Feature", properties: { site_id: s.site_id, final_score: s.final_score }, geometry: s.geometry })),
    });
    (m.getSource("site-labels") as GeoJSONSource).setData({
      type: "FeatureCollection",
      features: sites.map((s) => ({ type: "Feature", properties: { rank: String(s.rank) }, geometry: { type: "Point", coordinates: s.centroid } })),
    });
    m.setFilter("sites-selected", ["==", ["get", "site_id"], selectedSiteId ?? ""]);
  }

  useEffect(sync, [p.bbox, p.tileUrl, p.opacity, p.project, p.sites, p.selectedSiteId]);

  useEffect(() => {
    if (!draw.current) return;
    draw.current.setMode(p.drawing ? "polygon" : "static");
  }, [p.drawing]);

  // Frame new results.
  useEffect(() => {
    const m = map.current;
    const geoms = [...(p.project ? [p.project] : []), ...p.sites.map((s) => s.geometry)];
    const b = boundsOf(geoms);
    if (m && b && p.sites.length) m.fitBounds(b, { padding: 60, maxZoom: 14, duration: 800 });
  }, [p.sites]);

  useEffect(() => {
    const m = map.current;
    const s = p.sites.find((x) => x.site_id === p.selectedSiteId);
    if (m && s) {
      const b = boundsOf([s.geometry]);
      if (b) m.fitBounds(b, { padding: 120, maxZoom: 15, duration: 700 });
    }
  }, [p.selectedSiteId]);

  useEffect(() => {
    const m = map.current;
    const b = p.project && boundsOf([p.project]);
    if (m && b && !p.sites.length) m.fitBounds(b, { padding: 80, maxZoom: 14, duration: 700 });
  }, [p.project]);

  return <div ref={el} className="map" />;
}
