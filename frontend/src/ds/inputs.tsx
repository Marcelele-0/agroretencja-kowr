import { useId, type CSSProperties, type FormEvent } from "react";
import { CROP_GROUPS, type CropCode } from "../lib/crops";
import { addDays, formatLong, inSeason, isoOf, parts, seasonYear, todayIso, type Iso } from "../lib/dates";
import { Button, cx, Icon, Pill } from "./core";

export function ParcelSearch({ value, onChange, onSubmit, busy, error, placeholder, style, className }: {
  value: string; onChange: (v: string) => void; onSubmit: (v: string) => void; busy?: boolean; error?: string | null; placeholder?: string; style?: CSSProperties; className?: string;
}) {
  const id = useId();
  const submit = (e: FormEvent) => { e.preventDefault(); if (value.trim()) onSubmit(value.trim()); };
  return (
    <div className={cx("rs-search", className)} style={style}>
      <form className="ar-search" role="search" onSubmit={submit}>
        <Icon name="search" />
        <input value={value} onChange={(e) => onChange(e.target.value)} placeholder={placeholder ?? "Miejscowość lub numer działki, np. Gniezno"} aria-label="Numer działki"
          aria-describedby={error ? `${id}e` : undefined} aria-invalid={error ? true : undefined} spellCheck={false} autoComplete="off" />
        <Button variant="primary" type="submit" disabled={busy}>{busy ? "Szukam…" : "Szukaj"}</Button>
      </form>
      {error ? <p className="rs-search__error" id={`${id}e`} role="alert">{error}</p> : null}
    </div>
  );
}

export function seasonHint(d: Iso): string {
  return inSeason(d) ? "Okna IUNG obejmują 21.III–30.IX." : `Poza sezonem: pokażemy sezon ${seasonYear(d)} i plan przed wiosną.`;
}
export function dateBounds(today: Iso = todayIso()): { min: Iso; max: Iso } {
  const [y, m, d] = parts(today);
  return { min: isoOf(y - 10, m, Math.min(d, 28)), max: today };
}
export function quickPicks(today: Iso = todayIso()): { label: string; date: Iso }[] {
  const [y, m, d] = parts(today);
  const peakYear = m > 7 || (m === 7 && d >= 15) ? y : y - 1;
  const picks = [{ label: "Dziś", date: today }, { label: `Szczyt sezonu ${peakYear}`, date: isoOf(peakYear, 7, 15) }];
  if ("2019-07-15" >= dateBounds(today).min && "2019-07-15" <= today) {
    picks.push({ label: "Susza 2019", date: "2019-07-15" });
  }
  return picks;
}

export function CropSelect({ value, onChange, label, className }: { value: CropCode; onChange: (c: CropCode) => void; label: string; className?: string }) {
  return (
    <select className={cx("ar-select", className)} value={value} aria-label={label} onChange={(e) => onChange(e.target.value as CropCode)}>
      {CROP_GROUPS.map((g) => <optgroup key={g.label} label={g.label}>{g.crops.map((c) => <option key={c.code} value={c.code}>{c.label}</option>)}</optgroup>)}
    </select>
  );
}

export function CropDateForm({ crop, date, onCrop, onDate, onSubmit, disabled, busy, submitLabel = "Analizuj działkę", layout = "row", hideCrop, hideSubmit, style, className }: {
  hideCrop?: boolean; hideSubmit?: boolean; crop: CropCode; date: Iso; onCrop: (c: CropCode) => void; onDate: (d: Iso) => void; onSubmit: () => void; disabled?: boolean; busy?: boolean; submitLabel?: string; layout?: "row" | "stack"; style?: CSSProperties; className?: string;
}) {
  const id = useId();
  const { min, max } = dateBounds();
  return (
    <form className={cx("ar-form", layout === "stack" && "ar-form--stack", className)} style={style} onSubmit={(e) => { e.preventDefault(); if (!disabled) onSubmit(); }}>
      {hideCrop ? null : <div className="ar-form__cell">
        <label className="ar-form__label" htmlFor={`${id}c`}>Uprawa</label>
        <select id={`${id}c`} className="ar-select" value={crop} onChange={(e) => onCrop(e.target.value as CropCode)}>
          {CROP_GROUPS.map((g) => <optgroup key={g.label} label={g.label}>{g.crops.map((c) => <option key={c.code} value={c.code}>{c.label}</option>)}</optgroup>)}
        </select>
      </div>}
      <div className="ar-form__cell">
        <label className="ar-form__label" htmlFor={`${id}d`}>Data analizy</label>
        <input id={`${id}d`} type="date" className="ar-input ar-input--data" value={date} min={min} max={max} required onChange={(e) => e.target.value && onDate(e.target.value)} />
        <div className="ar-form__chips">{quickPicks().map((p) => <Pill key={p.label} pressed={date === p.date} onClick={() => onDate(p.date)}>{p.label}</Pill>)}</div>
        <span className="ar-form__hint">{seasonHint(date)}</span>
      </div>
      {hideSubmit ? null : <div className="ar-form__cell ar-form__cell--submit">
        <Button variant="primary" size="lg" arrow type="submit" disabled={disabled || busy}>{busy ? "Analizuję…" : submitLabel}</Button>
      </div>}
    </form>
  );
}

/** Analysis date as a readable bar: "10 lipca 2019" with day steps, a native calendar under the date and quick picks. */
export function DateBar({ date, onDate, className }: { date: Iso; onDate: (d: Iso) => void; className?: string }) {
  const id = useId();
  const { min, max } = dateBounds();
  const step = (n: number) => { const d = addDays(date, n); if (d >= min && d <= max) onDate(d); };
  return (
    <div className={cx("rs-datebar", className)} role="group" aria-labelledby={`${id}l`}>
      <span className="ar-form__label" id={`${id}l`}>Analiza na dzień</span>
      <div className="rs-datebar__bar">
        <div className="rs-datebar__step">
          <Button variant="icon" size="sm" icon="back" label="Dzień wcześniej" onClick={() => step(-1)} disabled={date <= min} />
          <label className="rs-datebar__date">
            <span aria-hidden="true">{formatLong(date)}</span>
            <Icon name="calendar" />
            <input type="date" value={date} min={min} max={max} aria-label={`Data analizy: ${formatLong(date)}. Zmień datę`} onChange={(e) => e.target.value && onDate(e.target.value)} />
          </label>
          <Button variant="icon" size="sm" icon="arrow" label="Dzień później" onClick={() => step(1)} disabled={date >= max} />
        </div>
        <div className="rs-datebar__picks">{quickPicks().map((p) => <Pill key={p.label} pressed={date === p.date} onClick={() => onDate(p.date)}>{p.label}</Pill>)}</div>
      </div>
    </div>
  );
}
