/** Crops of the IUNG-PIB drought monitor (SMSR) plus grassland, which SMSR does not assess. */
export type CropCode =
  | "zboza_ozime" | "zboza_jare" | "kukurydza_ziarno" | "kukurydza_kiszonka" | "rzepak" | "ziemniak" | "burak_cukrowy"
  | "straczkowe" | "warzywa" | "chmiel" | "tyton" | "drzewa_owocowe" | "krzewy_owocowe" | "truskawki" | "uzytki_zielone";

export interface Crop { code: CropCode; label: string; /** Row name in the IUNG threshold table; null = no IUNG threshold. */ iung: string | null }

export const CROP_GROUPS: { label: string; crops: Crop[] }[] = [
  { label: "Grunty orne", crops: [
    { code: "zboza_ozime", label: "Zboża ozime", iung: "Zboża ozime" },
    { code: "zboza_jare", label: "Zboża jare", iung: "Zboża jare" },
    { code: "kukurydza_ziarno", label: "Kukurydza na ziarno", iung: "Kukurydza na ziarno" },
    { code: "kukurydza_kiszonka", label: "Kukurydza na kiszonkę", iung: "Kukurydza na kiszonkę" },
    { code: "rzepak", label: "Rzepak i rzepik", iung: "Rzepak i rzepik" },
    { code: "ziemniak", label: "Ziemniak", iung: "Ziemniak" },
    { code: "burak_cukrowy", label: "Burak cukrowy", iung: "Burak cukrowy" },
    { code: "straczkowe", label: "Rośliny strączkowe", iung: "Rośliny strączkowe" },
    { code: "warzywa", label: "Warzywa gruntowe", iung: "Warzywa gruntowe" },
    { code: "chmiel", label: "Chmiel", iung: "Chmiel" },
    { code: "tyton", label: "Tytoń", iung: "Tytoń" },
  ] },
  { label: "Sady i jagody", crops: [
    { code: "drzewa_owocowe", label: "Drzewa owocowe", iung: "Drzewa owocowe" },
    { code: "krzewy_owocowe", label: "Krzewy owocowe", iung: "Krzewy owocowe" },
    { code: "truskawki", label: "Truskawki", iung: "Truskawki" },
  ] },
  { label: "Użytki zielone", crops: [{ code: "uzytki_zielone", label: "Łąki i pastwiska", iung: null }] },
];

export const CROPS: Crop[] = CROP_GROUPS.flatMap((g) => g.crops);
export const DEFAULT_CROP: CropCode = "kukurydza_ziarno";

export function isCropCode(s: string | null | undefined): s is CropCode {
  return CROPS.some((c) => c.code === s);
}
export function cropOf(code: CropCode): Crop {
  return CROPS.find((c) => c.code === code)!;
}
/** Crop code of the backend contract (src/hydro_core/schemas.py CropCode); every UI crop has a full backend analysis. */
const BACKEND_CROP: Record<CropCode, string> = {
  kukurydza_ziarno: "kukurydza", kukurydza_kiszonka: "kukurydza_kiszonka", zboza_ozime: "pszenica_ozima", zboza_jare: "pszenica_jara",
  rzepak: "rzepak_ozimy", burak_cukrowy: "burak_cukrowy", ziemniak: "ziemniak", uzytki_zielone: "uzytki_zielone",
  straczkowe: "straczkowe", warzywa: "warzywa", chmiel: "chmiel", tyton: "tyton",
  drzewa_owocowe: "drzewa_owocowe", krzewy_owocowe: "krzewy_owocowe", truskawki: "truskawki",
};
export function backendCrop(code: CropCode): string | null {
  return BACKEND_CROP[code] ?? null;
}
