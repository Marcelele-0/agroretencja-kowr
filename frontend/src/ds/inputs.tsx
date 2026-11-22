import { useEffect, useId, useRef, useState, type CSSProperties, type FormEvent, type KeyboardEvent as ReactKeyboardEvent } from "react";
import { createPortal } from "react-dom";
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

const CROP_LIST = CROP_GROUPS.flatMap((g) => g.crops);

/** Crop picker: a quiet text trigger and a grouped list card (bottom sheet on phones), keyboard operable like a listbox. */
export function CropSelect({ value, onChange, label, className, id: idProp }: { value: CropCode; onChange: (c: CropCode) => void; label: string; className?: string; id?: string }) {
  const autoId = useId();
  const id = idProp ?? autoId;
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const [pos, setPos] = useState<{ left: number; top: number; width: number; up: boolean } | null>(null);
  const trig = useRef<HTMLButtonElement>(null);
  const list = useRef<HTMLDivElement>(null);
  const current = CROP_LIST.find((c) => c.code === value);

  const place = () => {
    const r = trig.current?.getBoundingClientRect();
    if (!r) return;
    const width = Math.max(r.width, 300);
    const left = Math.min(Math.max(8, r.left), window.innerWidth - width - 8);
    const up = r.bottom + 360 > window.innerHeight && r.top > 360;
    setPos({ left, top: up ? r.top - 6 : r.bottom + 6, width, up });
  };
  const show = () => { place(); setActive(Math.max(0, CROP_LIST.findIndex((c) => c.code === value))); setOpen(true); };
  const close = (focus = true) => { setOpen(false); if (focus) trig.current?.focus(); };
  const pick = (code: CropCode) => { if (code !== value) onChange(code); close(); };

  useEffect(() => {
    if (!open) return;
    list.current?.focus();
    const away = (e: PointerEvent) => {
      const t = e.target as Node;
      if (!list.current?.contains(t) && !trig.current?.contains(t)) close(false);
    };
    const reflow = () => place();
    document.addEventListener("pointerdown", away);
    window.addEventListener("resize", reflow);
    window.addEventListener("scroll", reflow, true);
    return () => { document.removeEventListener("pointerdown", away); window.removeEventListener("resize", reflow); window.removeEventListener("scroll", reflow, true); };
  }, [open]);

  useEffect(() => {
    if (open) list.current?.querySelector(`[data-i="${active}"]`)?.scrollIntoView({ block: "nearest" });
  }, [open, active]);

  const onKey = (e: ReactKeyboardEvent) => {
    const n = CROP_LIST.length;
    if (e.key === "ArrowDown") setActive((i) => (i + 1) % n);
    else if (e.key === "ArrowUp") setActive((i) => (i - 1 + n) % n);
    else if (e.key === "Home") setActive(0);
    else if (e.key === "End") setActive(n - 1);
    else if (e.key === "Enter" || e.key === " ") pick(CROP_LIST[active]!.code);
    else if (e.key === "Escape" || e.key === "Tab") { close(e.key === "Escape"); if (e.key === "Tab") return; }
    else if (e.key.length === 1) {
      const k = e.key.toLocaleLowerCase("pl");
      const from = (active + 1) % n;
      const hit = [...CROP_LIST.slice(from), ...CROP_LIST.slice(0, from)].find((c) => c.label.toLocaleLowerCase("pl").startsWith(k));
      if (hit) setActive(CROP_LIST.indexOf(hit));
      else return;
    } else return;
    e.preventDefault();
  };

  let i = -1;
  return (
    <>
      <button ref={trig} id={id} type="button" className={cx("rs-crop", open && "is-open", className)} aria-haspopup="listbox" aria-expanded={open} aria-label={`${label}: ${current?.label ?? ""}`}
        onClick={() => (open ? close() : show())} onKeyDown={(e) => { if (!open && (e.key === "ArrowDown" || e.key === "ArrowUp")) { e.preventDefault(); show(); } }}>
        <span className="rs-crop__value">{current?.label ?? "Wybierz uprawę"}</span>
        <svg className="rs-crop__chev" viewBox="0 0 12 12" aria-hidden="true"><path d="M3 4.5 6 7.5 9 4.5" /></svg>
      </button>
      {open && pos ? createPortal(
        <div className="rs-crop-layer">
          <div className="rs-crop-layer__scrim" aria-hidden="true" />
          <div ref={list} className={cx("rs-crop-menu", pos.up && "is-up")} role="listbox" tabIndex={-1} aria-label={label} aria-activedescendant={`${id}-o${active}`} onKeyDown={onKey}
            style={{ left: pos.left, top: pos.top, width: pos.width } as CSSProperties}>
            {CROP_GROUPS.map((g) => (
              <div key={g.label} className="rs-crop-menu__group" role="group" aria-label={g.label}>
                <div className="rs-crop-menu__head" aria-hidden="true">{g.label}</div>
                {g.crops.map((c) => {
                  i += 1;
                  const idx = i;
                  const sel = c.code === value;
                  return (
                    <div key={c.code} id={`${id}-o${idx}`} data-i={idx} role="option" aria-selected={sel} className={cx("rs-crop-menu__opt", sel && "is-sel", idx === active && "is-active")}
                      onPointerEnter={() => setActive(idx)} onClick={() => pick(c.code)}>
                      <span>{c.label}</span>
                      {sel ? <Icon name="check" /> : null}
                    </div>
                  );
                })}
              </div>
            ))}
          </div>
        </div>, document.body) : null}
    </>
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
        <CropSelect id={`${id}c`} value={crop} onChange={onCrop} label="Uprawa" />
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
