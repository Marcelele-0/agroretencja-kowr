/** ISO calendar date "YYYY-MM-DD". All arithmetic runs on UTC day numbers, so time zones never shift a date. */
export type Iso = string;

const GEN = ["stycznia", "lutego", "marca", "kwietnia", "maja", "czerwca", "lipca", "sierpnia", "września", "października", "listopada", "grudnia"];
const NOM = ["styczeń", "luty", "marzec", "kwiecień", "maj", "czerwiec", "lipiec", "sierpień", "wrzesień", "październik", "listopad", "grudzień"];
const ROMAN = ["I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X", "XI", "XII"];

export function parts(iso: Iso): [number, number, number] {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso);
  if (!m) throw new Error(`Zła data: ${iso}`);
  return [Number(m[1]), Number(m[2]), Number(m[3])];
}
export function isIso(s: string | null | undefined): s is Iso {
  if (!s || !/^\d{4}-\d{2}-\d{2}$/.test(s)) return false;
  const [y, m, d] = parts(s);
  return fromDay(toDay(s)) === s && m >= 1 && m <= 12 && d >= 1 && y > 1900;
}
export function isoOf(y: number, m: number, d: number): Iso {
  return `${String(y).padStart(4, "0")}-${String(m).padStart(2, "0")}-${String(d).padStart(2, "0")}`;
}
export function toDay(iso: Iso): number {
  const [y, m, d] = parts(iso);
  return Date.UTC(y, m - 1, d) / 86_400_000;
}
export function fromDay(n: number): Iso {
  return new Date(n * 86_400_000).toISOString().slice(0, 10);
}
export function addDays(iso: Iso, n: number): Iso {
  return fromDay(toDay(iso) + n);
}
export function minIso(a: Iso, b: Iso): Iso {
  return a < b ? a : b;
}
/** Today in the user's local calendar. */
export function todayIso(now: Date = new Date()): Iso {
  return isoOf(now.getFullYear(), now.getMonth() + 1, now.getDate());
}
export function lastDayOfMonth(y: number, m: number): number {
  return new Date(Date.UTC(y, m, 0)).getUTCDate();
}

/** "20 lipca 2018" */
export function formatLong(iso: Iso): string {
  const [y, m, d] = parts(iso);
  return `${d} ${GEN[m - 1]} ${y}`;
}
/** "20 lipca" */
export function formatDayMonth(iso: Iso): string {
  const [, m, d] = parts(iso);
  return `${d} ${GEN[m - 1]}`;
}
export function monthName(m: number): string {
  return NOM[m - 1] ?? "";
}
/** "20.VII" — the notation of the IUNG drought monitor. */
export function romanDate(m: number, d: number): string {
  return `${d}.${ROMAN[m - 1]}`;
}

/** The IUNG season: 21 March to 30 September. */
export function inSeason(iso: Iso): boolean {
  const [, m, d] = parts(iso);
  if (m < 3 || m > 9) return false;
  return !(m === 3 && d < 21);
}
/** The season a date belongs to: before 21 March it is the previous year's season. */
export function seasonYear(iso: Iso): number {
  const [y, m, d] = parts(iso);
  return m < 3 || (m === 3 && d < 21) ? y - 1 : y;
}
