import type { Geometry } from "./api";

/** Read a footprint from an uploaded GeoJSON or KML file. Returns the first (multi)polygon. */
export async function readFootprint(file: File): Promise<Geometry> {
  const text = await file.text();
  if (/\.kml$/i.test(file.name) || text.trimStart().startsWith("<")) return fromKml(text);
  return fromGeoJson(JSON.parse(text));
}

function fromGeoJson(gj: any): Geometry {
  const geoms: any[] =
    gj.type === "FeatureCollection" ? gj.features.map((f: any) => f.geometry)
    : gj.type === "Feature" ? [gj.geometry]
    : [gj];
  const polys = geoms.filter((g) => g && (g.type === "Polygon" || g.type === "MultiPolygon"));
  if (!polys.length) throw new Error("No polygon found in the file");
  if (polys.length === 1) return polys[0];
  return { type: "MultiPolygon", coordinates: polys.flatMap((g) => (g.type === "Polygon" ? [g.coordinates] : g.coordinates)) };
}

function fromKml(text: string): Geometry {
  const doc = new DOMParser().parseFromString(text, "application/xml");
  const rings = [...doc.getElementsByTagName("Polygon")].map((poly) => {
    const outer = poly.getElementsByTagName("outerBoundaryIs")[0] ?? poly;
    const coords = outer.getElementsByTagName("coordinates")[0]?.textContent ?? "";
    return coords.trim().split(/\s+/).map((t) => t.split(",").slice(0, 2).map(Number) as [number, number]);
  }).filter((r) => r.length >= 4);
  if (!rings.length) throw new Error("No polygon found in the KML");
  return rings.length === 1
    ? { type: "Polygon", coordinates: [rings[0]] }
    : { type: "MultiPolygon", coordinates: rings.map((r) => [r]) };
}

/** Approximate area in hectares (spherical excess on a small region). */
export function areaHa(g: Geometry): number {
  const R = 6371008.8;
  const ring = (c: number[][]) => {
    let s = 0;
    for (let i = 0; i < c.length - 1; i++) {
      const [x1, y1] = c[i], [x2, y2] = c[i + 1];
      s += ((x2 - x1) * Math.PI / 180) * (2 + Math.sin(y1 * Math.PI / 180) + Math.sin(y2 * Math.PI / 180));
    }
    return Math.abs(s * R * R / 2);
  };
  const polys = g.type === "Polygon" ? [g.coordinates] : g.coordinates;
  return polys.reduce((a, p) => a + ring(p[0]) - p.slice(1).reduce((h, r) => h + ring(r), 0), 0) / 1e4;
}

export const fmt = (n: number | null | undefined, d = 0) =>
  n == null ? "–" : n.toLocaleString("en-IN", { maximumFractionDigits: d, minimumFractionDigits: d });
