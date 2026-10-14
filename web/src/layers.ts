import type { LayerId } from "./api";

export const LAYERS: { id: LayerId; label: string; hint: string; legend?: { label: string; color: string }[]; ramp?: { palette: string[]; lo: string; hi: string } }[] = [
  { id: "true_colour", label: "Satellite", hint: "Sentinel-2 true colour, dry season 2025-26, 10 m" },
  {
    id: "land_cover",
    label: "Land cover",
    hint: "Dynamic World, most frequent class in 2025",
    legend: [
      { label: "Trees", color: "#397d49" },
      { label: "Scrub", color: "#dfc35a" },
      { label: "Grass", color: "#88b053" },
      { label: "Crops", color: "#e49635" },
      { label: "Built", color: "#c4281b" },
      { label: "Bare", color: "#a59b8f" },
      { label: "Water", color: "#419bdf" },
    ],
  },
  { id: "ndvi", label: "Vegetation health", hint: "NDVI from Sentinel-2", ramp: { palette: ["8c510a", "d8b365", "f6e8c3", "c7eae5", "5ab4ac", "01665e"], lo: "Bare", hi: "Dense green" } },
  { id: "similarity", label: "Like Mangar Bani", hint: "AlphaEarth embedding similarity to an intact forest patch", ramp: { palette: ["000004", "8c2981", "fe9f6d", "fcfdbf"], lo: "Different", hi: "Very similar" } },
  { id: "rainfall", label: "Rainfall", hint: "CHIRPS total rainfall 2025", ramp: { palette: ["fff7bc", "41b6c4", "253494"], lo: "300 mm", hi: "1000 mm" } },
  { id: "slope", label: "Slope", hint: "Copernicus DEM", ramp: { palette: ["ffffff", "fdae61", "d7191c"], lo: "Flat", hi: "25°+" } },
];

export const LC_COLORS: Record<string, string> = {
  trees: "#397d49", shrub_and_scrub: "#dfc35a", grass: "#88b053", crops: "#e49635",
  built: "#c4281b", bare: "#a59b8f", water: "#419bdf", flooded_vegetation: "#7a87c6", snow_and_ice: "#b39fe1",
};

export const LC_LABELS: Record<string, string> = {
  trees: "Trees", shrub_and_scrub: "Scrub", grass: "Grass", crops: "Crops", built: "Built-up",
  bare: "Bare", water: "Water", flooded_vegetation: "Wetland", snow_and_ice: "Snow",
};
