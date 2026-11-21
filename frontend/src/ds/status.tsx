import type { ReactNode } from "react";
import { cx } from "./core";

/** Water state: −5 (susza skrajna) … 0 (optimum) … +5 (zalanie). */
export const BAL: Record<string, string> = {
  "-5": "Susza skrajna", "-4": "Susza silna", "-3": "Susza", "-2": "Niedobór wody", "-1": "Lekki niedobór", "0": "Optimum",
  "1": "Lekki nadmiar", "2": "Nadmiar wody", "3": "Przewilgocenie", "4": "Silne przewilgocenie", "5": "Zalanie",
};
export function balStep(v: number | null | undefined): number | null {
  if (v === null || v === undefined || !Number.isFinite(v)) return null;
  return Math.max(-5, Math.min(5, Math.round(v)));
}
export function balKey(n: number): string { return n < 0 ? `m${-n}` : n > 0 ? `p${n}` : "0"; }
export function balCls(v: number | null): string { const n = balStep(v); return n === null ? "" : `ar-b-${balKey(n)}`; }
export function signed(n: number): string { return n > 0 ? `+${n}` : n < 0 ? `−${-n}` : "0"; }

const STEPS = [-5, -4, -3, -2, -1, 0, 1, 2, 3, 4, 5];

/** Water state for farmers: the word and the scale graphic. Numbers (−5…+5) only with `numbers` (method page, experts). */
export function WaterBalance({ value, label = "Stan wody", size = "lg", tone, numbers = false, caption, className }: {
  value: number | null; label?: string; size?: "lg" | "sm"; tone?: "surface" | "photo"; numbers?: boolean; caption?: ReactNode; className?: string;
}) {
  const n = balStep(value);
  const name = n === null ? "Brak danych" : BAL[String(n)]!;
  const aria = `${label}: ${name.toLowerCase()}`;
  const cells = STEPS.map((i) => <span key={i} className={cx("ar-bal__cell", `ar-b-${balKey(i)}`, i === n && "ar-bal__cell--on")} />);

  const read = <span className="ar-bal__read" aria-hidden="true">{numbers ? <span className="ar-bal__num">{n === null ? "—" : signed(n)}</span> : null}{n === null ? null : <i className={cx("rs-bal__dot", `ar-b-${balKey(n)}`)} />}<span className="ar-bal__name rs-bal__word">{name}</span></span>;
  if (size === "sm") {
    return <span className={cx("ar-bal", "ar-bal--sm", tone === "photo" && "ar-bal--photo", className)} role="img" aria-label={aria}><span className="ar-bal__cells" aria-hidden="true">{cells}</span>{read}</span>;
  }
  return (
    <div className={cx("ar-bal", tone === "photo" && "ar-bal--photo", className)} role="group" aria-label={aria}>
      <div className="ar-bal__head"><span className="ar-bal__label">{label}</span>{read}</div>
      <div className="ar-bal__cells" aria-hidden="true">{cells}</div>
      {numbers ? <div className="ar-bal__ticks" aria-hidden="true">{STEPS.map((i) => <span key={i} className={i === n ? "is-on" : ""}>{signed(i)}</span>)}</div> : null}
      <div className="ar-bal__ends" aria-hidden="true"><span>Susza</span><span>Optimum</span><span>Nadmiar wody</span></div>
      {caption ? <p className="ar-bal__caption">{caption}</p> : null}
    </div>
  );
}

export type RiskLevel = "niskie" | "umiarkowane" | "wysokie" | "krytyczne";
const RISK: Record<RiskLevel, number> = { niskie: 1, umiarkowane: 2, wysokie: 3, krytyczne: 4 };

export function RiskChip({ level, side, veto, vetoReason, tone, className }: { level: RiskLevel; side?: "dry" | "wet"; veto?: boolean; vetoReason?: string | null; tone?: "surface" | "photo"; className?: string }) {
  const n = RISK[level];
  const word = side === "wet" ? "Ryzyko nadmiaru wody" : side === "dry" ? "Ryzyko suszy" : "Ryzyko";
  return (
    <div className={cx("ar-risk", `ar-risk--l${n}`, side === "wet" && "ar-risk--wet", tone === "photo" && "ar-risk--photo", className)} role="status">
      <span className="ar-risk__text">{word} <b>{level}</b></span>
      <span className="ar-risk__meter" aria-hidden="true">{[1, 2, 3, 4].map((i) => <span key={i} className={cx("ar-risk__seg", i <= n && "ar-risk__seg--on")} />)}</span>
      {veto ? <span className="ar-risk__veto"><span>{vetoReason ?? "Ocena nie może być lepsza mimo innych wskaźników."}</span></span> : null}
    </div>
  );
}

export type ConfidenceLevel = "wysoka" | "srednia" | "ograniczona";
const CONF: Record<ConfidenceLevel, [string, number]> = { wysoka: ["wysoka", 3], srednia: ["średnia", 2], ograniczona: ["ograniczona", 1] };

export function ConfidenceBadge({ level, detail, tone, className }: { level: ConfidenceLevel; detail?: ReactNode; tone?: "surface" | "photo"; className?: string }) {
  const [word, n] = CONF[level];
  return (
    <div className={cx("ar-conf", tone === "photo" && "ar-conf--photo", className)}>
      <span className="ar-conf__text">Pewność <b>{word}</b></span>
      <span className="ar-conf__meter" aria-hidden="true">{[1, 2, 3].map((i) => <span key={i} className={cx("ar-conf__seg", i <= n && "ar-conf__seg--on")} />)}</span>
      {detail ? <span className="ar-conf__detail">{detail}</span> : null}
    </div>
  );
}
