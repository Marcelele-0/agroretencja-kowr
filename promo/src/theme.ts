import { loadFont as loadSans } from "@remotion/google-fonts/InstrumentSans";

const sans = loadSans("normal", { weights: ["400", "500", "600", "700"], subsets: ["latin", "latin-ext"] });

export const FONT = `${sans.fontFamily}, "Helvetica Neue", Arial, sans-serif`;
export const FPS = 30;
export const W = 1920;
export const H = 1080;

/** Design-system tokens of Rusałka (frontend/src/styles/tokens.css). */
export const C = {
  night: "#0f1a14",
  onNight: "#eef2ea",
  lineNight: "#eef2ea40",
  lime: "#c6ee7c",
  forest: "#1d3a2a",
  photoInk: "#ffffff",
  scrim: "#0b1710b3",
  scrimSoft: "#0b171047",
  glassDark: "#0f1c14a3",
  glassLine: "#ffffff38",
  nav: "#121b16",
  muted: "#a3ada6",
};

/** Water-state scale −5…+5, dry to wet. */
export const SCALE = ["#7a1a10", "#b02e1c", "#e0703f", "#f09a52", "#f4d08c", "#62b86c", "#a8dae2", "#62b2da", "#2873b5", "#1c4f9c", "#13265f"];
