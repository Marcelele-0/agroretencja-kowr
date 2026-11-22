/** Retention and field measures shown when the backend ranking is not available. Effects are phrases, never invented numbers. */
export interface Measure {
  id: string;
  title: string;
  effect: string;
  /** The measure changes a water device: it needs a decision of Wody Polskie. */
  permit: boolean;
  /** Assumed effect in the what-if simulation: extra water kept per 60 days [mm] and cut of evaporation [0–1]. */
  sim?: { addMm?: number; etCut?: number; note: string };
}
export const PERMIT_URL = "https://www.wody.gov.pl";

export const DRY_MEASURES: Measure[] = [
  { id: "miedzyplon_mulcz", title: "Międzyplon i mulcz", effect: "Mniej parowania z gleby, więcej próchnicy", permit: false, sim: { etCut: 0.1, note: "−10% parowania" } },
  { id: "uprawa_bezorkowa", title: "Uprawa bez orki", effect: "Gleba traci mniej wody przy uprawie", permit: false, sim: { etCut: 0.05, note: "−5% parowania" } },
  { id: "pas_buforowy", title: "Pas buforowy w linii spływu", effect: "Woda z ulewy wsiąka, zamiast spływać", permit: false, sim: { addMm: 10, note: "+10 mm wody na 60 dni" } },
  { id: "zastawka", title: "Zastawka na rowie", effect: "Woda z roztopów zostaje w rowie i w glebie", permit: true, sim: { addMm: 25, note: "+25 mm wody na 60 dni" } },
];
export const WET_MEASURES: Measure[] = [
  { id: "odloz_prace", title: "Odłóż prace polowe", effect: "Ciężki sprzęt nie ugniata mokrej gleby", permit: false },
  { id: "pas_buforowy", title: "Pas buforowy w linii spływu", effect: "Spływ nie zabiera gleby z działki", permit: false },
  { id: "zastawka", title: "Zastawka na rowie", effect: "Nadmiar wody zostaje na suche miesiące", permit: true },
];
/** Local list for a water state: excess-water measures from "nadmiar wody" (+2) up, retention measures otherwise. */
export function measuresForStage(stage: number | null | undefined): Measure[] {
  return (stage ?? 0) >= 2 ? WET_MEASURES : DRY_MEASURES;
}
/** Permit flag for a backend recommendation id or category. */
export function measureLook(idOrCategory: string): Pick<Measure, "permit"> {
  const all = [...DRY_MEASURES, ...WET_MEASURES];
  const hit = all.find((m) => idOrCategory.includes(m.id)) ?? null;
  const permit = /zastaw|pietrz|piętrz|zbiorn|row|staw|drenaz|drenaż|urzadzenie_wodne|odplyw|odpływ/i.test(idOrCategory);
  return { permit: hit?.permit ?? permit };
}
