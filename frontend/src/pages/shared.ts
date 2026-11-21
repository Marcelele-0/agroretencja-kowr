import type { CropCode } from "../lib/crops";
import type { Iso } from "../lib/dates";

export function analysisHref(parcelId: string, crop: CropCode, date: Iso): string {
  return `/analiza?${new URLSearchParams({ dzialka: parcelId, uprawa: crop, data: date })}`;
}
export function ha(v: number): string {
  return v.toLocaleString("pl-PL", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}
export function mm(v: number | null | undefined): string {
  return v === null || v === undefined ? "—" : String(Math.round(v)).replace("-", "−");
}
export function dec(v: number | null | undefined, d = 1): string {
  return v === null || v === undefined ? "—" : v.toLocaleString("pl-PL", { minimumFractionDigits: d, maximumFractionDigits: d });
}
export function timePl(d: Date | null): string {
  if (!d) return "przy wyświetlaniu mapy";
  return d.toLocaleString("pl-PL", { day: "numeric", month: "numeric", year: "numeric", hour: "2-digit", minute: "2-digit" });
}
