/** Retention and field measures shown when the backend ranking is not available. Effects are phrases, never invented numbers. */
export interface Measure {
  id: string;
  title: string;
  effect: string;
  /** The measure changes a water device: it needs a decision of Wody Polskie. */
  permit: boolean;
  image: string;
  /** Assumed effect in the what-if simulation: extra water kept per 60 days [mm] and cut of evaporation [0–1]. */
  sim?: { addMm?: number; etCut?: number; note: string };
}
export const PERMIT_URL = "https://www.wody.gov.pl";

const IMG = { aerial: "/img/pola-konturowe.jpg", grass: "/img/trawa-rosa.jpg", water: "/img/rozlewiska-biebrza.jpg", moss: "/img/mech-kloda.jpg" };

export const DRY_MEASURES: Measure[] = [
  { id: "miedzyplon_mulcz", title: "Międzyplon i mulcz", effect: "Mniej parowania z gleby, więcej próchnicy", permit: false, image: IMG.aerial, sim: { etCut: 0.1, note: "−10% parowania" } },
  { id: "uprawa_bezorkowa", title: "Uprawa bez orki", effect: "Gleba traci mniej wody przy uprawie", permit: false, image: IMG.moss, sim: { etCut: 0.05, note: "−5% parowania" } },
  { id: "pas_buforowy", title: "Pas buforowy w linii spływu", effect: "Woda z ulewy wsiąka, zamiast spływać", permit: false, image: IMG.grass, sim: { addMm: 10, note: "+10 mm wody na 60 dni" } },
  { id: "zastawka", title: "Zastawka na rowie", effect: "Woda z roztopów zostaje w rowie i w glebie", permit: true, image: IMG.water, sim: { addMm: 25, note: "+25 mm wody na 60 dni" } },
];
export const WET_MEASURES: Measure[] = [
  { id: "odloz_prace", title: "Odłóż prace polowe", effect: "Ciężki sprzęt nie ugniata mokrej gleby", permit: false, image: IMG.grass },
  { id: "pas_buforowy", title: "Pas buforowy w linii spływu", effect: "Spływ nie zabiera gleby z działki", permit: false, image: IMG.aerial },
  { id: "zastawka", title: "Zastawka na rowie", effect: "Nadmiar wody zostaje na suche miesiące", permit: true, image: IMG.water },
];
/** Image and permit flag for a backend recommendation id or category. */
export function measureLook(idOrCategory: string): Pick<Measure, "image" | "permit"> {
  const all = [...DRY_MEASURES, ...WET_MEASURES];
  const hit = all.find((m) => idOrCategory.includes(m.id)) ?? null;
  const permit = /zastaw|pietrz|piętrz|zbiorn|row|staw|drenaz|drenaż/i.test(idOrCategory);
  return { image: hit?.image ?? IMG.aerial, permit: hit?.permit ?? permit };
}

/** Simulation levers derived from backend retention metrics, falling back to local presets. */
export function simForBackend(r: { id: string; category: string; delta_taw_mm: number; runoff_reduction_pct: number }): Measure["sim"] {
  if (r.delta_taw_mm > 0 || r.runoff_reduction_pct > 0) {
    const addMm = Math.max(0, Math.round(r.delta_taw_mm));
    const etCut = r.runoff_reduction_pct > 0 ? Math.min(0.2, Math.round((r.runoff_reduction_pct / 200) * 100) / 100) : 0;
    const noteParts = [addMm > 0 ? `+${addMm} mm` : null, r.runoff_reduction_pct > 0 ? `−${Math.round(r.runoff_reduction_pct)}% spływu` : null].filter(Boolean);
    return { addMm, etCut, note: noteParts.join(", ") };
  }
  const key = `${r.id} ${r.category}`.toLowerCase();
  const hit = DRY_MEASURES.find((m) => key.includes(m.id));
  return hit?.sim;
}
