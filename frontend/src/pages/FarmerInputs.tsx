import { useEffect, useId, useState } from "react";
import type { RainGaugeCorrection } from "../api/contract";
import { farmerData, GAUGE_MAX_MM, parseMm, setDrained, setGaugeRain, useFarmerVersion } from "../analysis/farmerData";
import { Button, Pill } from "../ds";
import { addDays, formatDayMonth, formatLong, type Iso } from "../lib/dates";
import { dec } from "./shared";

/**
 * What the farmer knows better than the public maps: tile drainage on the parcel and the rain measured
 * on it in the last 7 days. Each answer re-runs the backend analysis (kept in this browser only).
 */
export function FarmerInputs({ parcelId, date, gauge }: { parcelId: string; date: Iso; gauge: RainGaugeCorrection | null }) {
  useFarmerVersion();
  const id = useId();
  const data = farmerData(parcelId);
  const saved = data.rain7d[date];
  const savedText = saved === undefined ? "" : dec(saved, saved % 1 ? 1 : 0);
  const [draft, setDraft] = useState(savedText);
  useEffect(() => setDraft(savedText), [parcelId, date, savedText]);
  const parsed = parseMm(draft);
  const invalid = draft.trim() !== "" && parsed === null;
  const given = data.drained !== null || saved !== undefined;
  const from = addDays(date, -6);

  return (
    <details className="rs-farmer" open={given || undefined}>
      <summary className="rs-farmer__sum">
        <span>Popraw dokładność oceny</span>
        <span className="ar-muted">{given ? "uwzględniono Twoje dane" : "drenaż i opad z deszczomierza"}</span>
      </summary>
      <div className="rs-farmer__body">
        <div className="rs-farmer__field" role="group" aria-labelledby={`${id}d`}>
          <span id={`${id}d`} className="ar-form__label">Czy działka ma drenaż rurkowy?</span>
          <div className="ar-form__chips">
            <Pill pressed={data.drained === true} onClick={() => setDrained(parcelId, true)}>Tak</Pill>
            <Pill pressed={data.drained === false} onClick={() => setDrained(parcelId, false)}>Nie</Pill>
            <Pill pressed={data.drained === null} onClick={() => setDrained(parcelId, null)}>Nie wiem</Pill>
          </div>
          <p className="rs-farmer__hint ar-muted">
            {data.drained === true
              ? "Bilans zakłada odpływ nadmiaru wody przez dreny; w planie retencji pojawia się sterowany odpływ z drenażu."
              : data.drained === false
                ? "Działka bez drenów: w planie retencji nie proponujemy sterowanego odpływu z drenażu."
                : "Mapy drenowań nie są publiczne; bez odpowiedzi drenaż oceniamy z sieci rowów melioracyjnych."}
          </p>
        </div>

        <form
          className="rs-farmer__field"
          onSubmit={(e) => {
            e.preventDefault();
            if (parsed !== null) setGaugeRain(parcelId, date, parsed);
          }}
        >
          <label className="ar-form__label" htmlFor={`${id}r`}>
            Opad z Twojego deszczomierza, {formatDayMonth(from)} – {formatLong(date)} (7 dni)
          </label>
          <div className="rs-farmer__rain">
            <input
              id={`${id}r`}
              className="ar-input rs-farmer__input"
              type="text"
              inputMode="decimal"
              autoComplete="off"
              placeholder="np. 12,5"
              value={draft}
              aria-invalid={invalid || undefined}
              aria-describedby={`${id}h`}
              onChange={(e) => setDraft(e.target.value)}
            />
            <span className="rs-farmer__unit">mm</span>
            <Button variant="outline" size="sm" type="submit" disabled={parsed === null || parsed === saved}>Uwzględnij</Button>
            {saved !== undefined ? (
              <Button variant="light" size="sm" type="button" onClick={() => setGaugeRain(parcelId, date, null)}>Usuń</Button>
            ) : null}
          </div>
          <p id={`${id}h`} className="rs-farmer__hint ar-muted" role={invalid ? "alert" : undefined}>
            {invalid
              ? `Wpisz liczbę od 0 do ${GAUGE_MAX_MM} mm.`
              : gauge
                ? `Uwzględniono ${dec(gauge.gauge_mm)} mm z deszczomierza zamiast ${dec(gauge.grid_mm)} mm z siatki modelu.`
                : "Suma z 7 dni zastąpi opad z siatki modelu (ok. 9–11 km) w bilansie wody tej działki."}
          </p>
        </form>
      </div>
    </details>
  );
}
