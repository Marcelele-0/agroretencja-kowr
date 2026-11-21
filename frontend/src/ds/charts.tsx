import type { CSSProperties, ReactNode } from "react";
import { cx, Icon, type IconName } from "./core";
import { BAL, balCls, balStep } from "./status";

export function MetricTile({ icon, label, value, unit, tone = "surface", className }: { icon?: IconName; label: ReactNode; value: ReactNode; unit?: string; tone?: "glass" | "surface"; className?: string }) {
  return (
    <div className={cx("ar-metric", `ar-metric--${tone}`, className)}>
      <span className="ar-metric__label">{icon ? <Icon name={icon} /> : null}{label}</span>
      <span className="ar-metric__value">{value}{unit ? <span className="ar-metric__unit">{unit}</span> : null}</span>
    </div>
  );
}

export function ProgressBar({ label, value, max = 100, valueText, tone }: { label: string; value: number; max?: number; valueText?: string; tone?: "default" | "water" }) {
  const p = Math.max(0, Math.min(100, (value / max) * 100));
  return (
    <div className={cx("ar-progress", tone === "water" && "ar-progress--water")}>
      <div className="ar-progress__head"><span className="ar-progress__label">{label}</span><span className="ar-progress__val">{valueText ?? `${value} / ${max}`}</span></div>
      <div className="ar-progress__track" role="progressbar" aria-valuemin={0} aria-valuemax={max} aria-valuenow={value} aria-label={label}><div className="ar-progress__fill" style={{ width: `${p}%` }} /></div>
    </div>
  );
}

const IUNG_START = ["21.III", "1.IV", "11.IV", "21.IV", "1.V", "11.V", "21.V", "1.VI", "11.VI", "21.VI", "1.VII", "11.VII", "21.VII", "1.VIII"];
const IUNG_END = ["20.V", "31.V", "10.VI", "20.VI", "30.VI", "10.VII", "20.VII", "31.VII", "10.VIII", "20.VIII", "31.VIII", "10.IX", "20.IX", "30.IX"];
const minus = (v: number) => String(v).replace("-", "−");

export interface KbwWindowValue { kbw: number | null; threshold: number | null }
export function KbwWindows({ windows, active, title, summary, thresholdLabel, legend = true, className }: {
  windows: KbwWindowValue[]; active?: number | null; title?: string; summary?: ReactNode; thresholdLabel?: string; legend?: boolean; className?: string;
}) {
  const w = windows.slice(0, 14);
  const vals = [0];
  w.forEach((x) => { if (x.kbw !== null) vals.push(x.kbw); if (x.threshold !== null) vals.push(x.threshold); });
  const lo = Math.min(...vals) * 1.1;
  let top = Math.max(...vals);
  top = top > 0 ? top * 1.2 : Math.abs(lo) * 0.08;
  const span = top - lo || 1;
  const y = (v: number) => ((top - v) / span) * 100;
  let dry = 0, known = 0;
  const cols = w.map((x, i) => {
    const hasK = x.kbw !== null, hasT = x.threshold !== null;
    const st = hasK && hasT && x.kbw! < x.threshold! ? "dry" : hasK && x.kbw! > 0 ? "wet" : "ok";
    if (hasK && hasT) known++;
    if (st === "dry") dry++;
    const on = i === active;
    const tip = `${IUNG_START[i]}–${IUNG_END[i]}: KBW ${hasK ? `${x.kbw} mm` : "brak danych"}${hasT ? `, próg ${x.threshold} mm` : ", bez progu"}`;
    return (
      <div key={i} className={cx("ar-kbw__col", `ar-kbw__col--${st}`, on && "ar-kbw__col--on")} title={tip}>
        {hasK ? <span className="ar-kbw__bar" style={{ top: `${y(Math.max(x.kbw!, 0))}%`, height: `${Math.abs(y(x.kbw!) - y(0))}%` }} /> : null}
        {hasT ? <span className="ar-kbw__th" style={{ top: `${y(x.threshold!)}%` }} /> : null}
        {on && hasK ? <span className="ar-kbw__val" style={{ top: `${y(Math.min(x.kbw!, 0))}%` }}>{minus(x.kbw!)}</span> : null}
      </div>
    );
  });
  const aria = w.map((x, i) => `${IUNG_START[i]}–${IUNG_END[i]} ${x.kbw === null ? "brak" : `${x.kbw} mm`}${x.threshold !== null ? ` (próg ${x.threshold})` : ""}`).join("; ");
  return (
    <figure className={cx("ar-kbw", className)} style={{ "--n": w.length || 14 } as CSSProperties}>
      <figcaption className="ar-kbw__head">
        <span className="ar-kbw__title">{title ?? "Klimatyczny bilans wodny w oknach IUNG"}</span>
        <span className="ar-kbw__sum">{summary ?? (known ? <>Susza wg progów IUNG w <b>{dry} z {known}</b> okien</> : "Brak progu IUNG dla tej uprawy")}</span>
      </figcaption>
      <div className="ar-kbw__plot" role="img" aria-label={`${title ?? "KBW w oknach IUNG"}. ${aria}`}>
        <span className="ar-kbw__zero" style={{ top: `${y(0)}%` }} aria-hidden="true" />
        {cols}
      </div>
      <div className="ar-kbw__x" aria-hidden="true">{w.map((_, i) => <span key={i} className={i === active ? "is-on" : ""}>{IUNG_START[i]}</span>)}</div>
      {legend ? (
        <div className="ar-kbw__legend">
          <span className="ar-kbw__key"><span className="ar-kbw__sw ar-kbw__sw--dry" />KBW poniżej progu</span>
          <span className="ar-kbw__key"><span className="ar-kbw__sw ar-kbw__sw--ok" />KBW powyżej progu</span>
          {known ? <span className="ar-kbw__key"><span className="ar-kbw__sw ar-kbw__sw--th" />{thresholdLabel ?? "Próg IUNG"}</span> : null}
          {active !== null && active !== undefined ? <span className="ar-kbw__key"><span className="ar-kbw__sw ar-kbw__sw--on" />Okno do {IUNG_END[active]}</span> : null}
        </div>
      ) : null}
    </figure>
  );
}

export function YearCompare({ years, highlightYear, title, summary, droughtAt = -3, children, className }: {
  years: { year: number; value: number | null }[]; highlightYear?: number; title?: string; summary?: ReactNode; droughtAt?: number; children?: ReactNode; className?: string;
}) {
  const dry = years.filter((y) => y.value !== null && balStep(y.value)! <= droughtAt).length;
  const label = years.map((y) => `${y.year}: ${y.value === null ? "brak danych" : BAL[String(balStep(y.value))]!.toLowerCase()}`).join(", ");
  return (
    <figure className={cx("ar-yrs", className)} style={{ "--n": years.length || 10 } as CSSProperties}>
      <figcaption className="ar-yrs__head">
        <span className="ar-yrs__title">{title ?? "Ten sam termin w każdym roku"}</span>
        <span className="ar-yrs__sum">{summary ?? <>Susza w <b>{dry} z {years.length}</b> lat</>}</span>
      </figcaption>
      <div className="ar-yrs__row" role="img" aria-label={`${title ?? "Stan wody w kolejnych latach"}. ${label}`}>
        {years.map((y) => {
          const n = balStep(y.value);
          return <span key={y.year} className={cx("ar-yrs__cell", n === null ? "ar-yrs__cell--none" : balCls(n), y.year === highlightYear && "ar-yrs__cell--on")} title={`${y.year}: ${n === null ? "brak danych" : BAL[String(n)]!.toLowerCase()}`} aria-hidden="true">{n === null ? "brak" : ""}</span>;
        })}
      </div>
      <div className="ar-yrs__years" aria-hidden="true">{years.map((y) => <span key={y.year} className={y.year === highlightYear ? "is-on" : ""}>{"’" + String(y.year).slice(2)}</span>)}</div>
      {children}
    </figure>
  );
}

const CAUSE_LABEL: Record<string, string> = { klimat: "Klimat", gleba: "Gleba", splyw: "Spływ", uprawa: "Uprawa" };
export function CauseBreakdown({ causes }: { causes: { key: string; pct: number; note?: string; label?: string }[] }) {
  return (
    <div className="ar-cause">
      <div className="ar-cause__bar" aria-hidden="true">{causes.map((c) => <span key={c.key} className={`ar-cause__seg ar-c-${c.key}`} style={{ flexGrow: Math.max(c.pct, 0.5), flexBasis: 0 }} />)}</div>
      <ul className="ar-cause__list">
        {causes.map((c) => (
          <li key={c.key} className="ar-cause__item">
            <span className={`ar-cause__sw ar-c-${c.key}`} aria-hidden="true" />
            <span className="ar-cause__name">{c.label ?? CAUSE_LABEL[c.key] ?? c.key}</span>
            <span className="ar-cause__pct">{Math.round(c.pct)}%</span>
            {c.note ? <span className="ar-cause__note">{c.note}</span> : null}
          </li>
        ))}
      </ul>
    </div>
  );
}

/** Daily water state on the −5…+5 scale: bars go down (drought) or up (excess) from 0; forecast days are dimmed with a dashed edge. */
export function StateTimeline({ days, selected, today, onPick, title, summary, compact }: {
  days: { date: string; value: number | null; forecast: boolean }[]; selected: string; today: string; onPick?: (date: string) => void; title?: string; summary?: ReactNode; compact?: boolean;
}) {
  const label = (d: string) => { const [, m, dd] = d.split("-"); return `${Number(dd)}.${Number(m)}`; };
  const aria = days.map((d) => `${d.date}: ${d.value === null ? "brak" : BAL[String(balStep(d.value))]!.toLowerCase()}${d.forecast ? " (prognoza)" : ""}`).join(", ");
  const hasForecast = days.some((d) => d.forecast);
  return (
    <figure className={cx("rs-st", compact && "rs-st--compact")} style={{ "--n": days.length } as CSSProperties}>
      {compact ? null : <figcaption className="ar-kbw__head">
        <span className="ar-kbw__title">{title ?? "Stan wody dzień po dniu"}</span>
        {summary ? <span className="ar-kbw__sum">{summary}</span> : null}
      </figcaption>}
      <div className="rs-st__wrap">
        {compact ? null : <div className="rs-st__y" aria-hidden="true"><span>nadmiar</span><span>optimum</span><span>susza</span></div>}
        <div className="rs-st__plot" role="img" aria-label={`${title ?? "Stan wody dzień po dniu"}. ${aria}`}>
          <span className="rs-st__zero" aria-hidden="true" />
          {days.map((d) => {
            const n = balStep(d.value);
            const h = n === null ? 0 : Math.max(Math.abs(n), 0.35) * 10;
            const bar = <span className={cx("rs-st__bar", n === null ? "" : balCls(n))} style={n === null ? undefined : n >= 0 ? { bottom: "50%", height: `${h}%` } : { top: "50%", height: `${h}%` }} />;
            const cls = cx("rs-st__day", d.forecast && "rs-st__day--fc", d.date === selected && "rs-st__day--on", d.date === today && "rs-st__day--today");
            const tip = `${label(d.date)}: ${n === null ? "brak danych" : BAL[String(n)]!.toLowerCase()}${d.forecast ? " (prognoza)" : ""}`;
            return onPick
              ? <button key={d.date} type="button" className={cls} title={tip} aria-label={`Pokaż ${tip}`} onClick={() => onPick(d.date)}>{bar}</button>
              : <span key={d.date} className={cls} title={tip}>{bar}</span>;
          })}
        </div>
      </div>
      {compact ? null : <div className="rs-st__x" aria-hidden="true">{days.map((d, i) => <span key={d.date}>{d.date === today ? "dziś" : i % 7 === 0 || d.date === selected ? label(d.date) : ""}</span>)}</div>}
      {compact ? null : <div className="ar-kbw__legend">
        <span className="ar-kbw__key"><span className="ar-kbw__sw ar-b-m3" />Obserwacja</span>
        {hasForecast ? <span className="ar-kbw__key"><span className="ar-kbw__sw rs-st__sw-fc" />Prognoza</span> : null}
        <span className="ar-kbw__key"><span className="ar-kbw__sw ar-kbw__sw--on" />Wybrany dzień{onPick ? " (kliknij inny dzień)" : ""}</span>
      </div>}
    </figure>
  );
}
