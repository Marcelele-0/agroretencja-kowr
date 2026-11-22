import { Wordmark } from "../ds";
import type { KbwWindow } from "../lib/kbw";
import type { ParcelReportSheet, ReportCauseRow, ReportDocument } from "./reportBuilder";

const STEPS = [-5, -4, -3, -2, -1, 0, 1, 2, 3, 4, 5] as const;
const STAGE_HEX: Record<number, string> = {
  [-5]: "#a12a22",
  [-4]: "#cf4629",
  [-3]: "#e77e3d",
  [-2]: "#edba4c",
  [-1]: "#f0df78",
  0: "#d3e37c",
  1: "#abd97d",
  2: "#6ebe7e",
  3: "#3da38f",
  4: "#247e93",
  5: "#1a4876",
};
const CAUSE_HEX: Record<ReportCauseRow["key"], string> = {
  klimat: "#e77e3d",
  gleba: "#8f673f",
  splyw: "#247e93",
  uprawa: "#3da38f",
};
const IUNG_SHORT = ["21.III", "1.IV", "11.IV", "21.IV", "1.V", "11.V", "21.V", "1.VI", "11.VI", "21.VI", "1.VII", "11.VII", "21.VII", "1.VIII"];

function stageHex(n: number | null): string {
  return n === null ? "#d5ddd6" : STAGE_HEX[n] ?? "#d3e37c";
}

function dayNum(iso: string): string {
  const [, m, d] = iso.split("-");
  return `${Number(d)}.${Number(m)}`;
}

function KbwSvgChart({ windows, active, season }: { windows: KbwWindow[]; active: number | null; season: number | null }) {
  const W = 430;
  const H = 132;
  const padL = 28;
  const padR = 8;
  const padT = 18;
  const padB = 20;
  const plotW = W - padL - padR;
  const plotH = H - padT - padB;
  const list = windows.slice(0, 14);
  const vals = [0];
  let dry = 0;
  let known = 0;
  for (const w of list) {
    if (w.kbw !== null) vals.push(w.kbw);
    if (w.threshold !== null) vals.push(w.threshold);
    if (w.kbw !== null && w.threshold !== null) known++;
    if (w.drought) dry++;
  }
  const lo = Math.min(...vals) * 1.12;
  const rawHi = Math.max(...vals);
  const hi = rawHi > 0 ? rawHi * 1.22 : Math.max(20, Math.abs(lo) * 0.1);
  const span = hi - lo || 1;
  const yOf = (v: number) => padT + ((hi - v) / span) * plotH;
  const y0 = yOf(0);
  const colW = plotW / (list.length || 14);
  const barW = Math.max(8, colW - 6);

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="rs-pdf-svg" role="img" aria-label="Okna IUNG KBW">
      <text x={0} y={11} fontSize="8.5" fontWeight="700" fill="#13211a">
        {`Okna KBW IUNG (sezon ${season ?? "—"})`}
      </text>
      <text x={W} y={11} fontSize="8" textAnchor="end" fill="#56625a">
        {known ? `Susza w ${dry} z ${known} okien` : "Brak progów IUNG"}
      </text>
      <line x1={padL} y1={y0} x2={W - padR} y2={y0} stroke="#6e7a72" strokeWidth="0.9" />
      <text x={padL - 4} y={y0 + 3} fontSize="7" textAnchor="end" fill="#56625a">0</text>
      {lo < -10 ? (
        <text x={padL - 4} y={H - padB} fontSize="6.5" textAnchor="end" fill="#56625a">
          {Math.round(lo)}
        </text>
      ) : null}
      {list.map((w, i) => {
        const cx = padL + i * colW + colW / 2;
        const bx = cx - barW / 2;
        const isOn = i === active;
        const hasK = w.kbw !== null;
        const hasT = w.threshold !== null;
        const barFill = w.drought ? "#cf4629" : hasK && w.kbw! > 0 ? "#3da38f" : "#6ebe7e";
        const yVal = hasK ? yOf(w.kbw!) : y0;
        const barY = Math.min(y0, yVal);
        const barH = Math.max(1.5, Math.abs(yVal - y0));
        return (
          <g key={w.index}>
            {isOn ? <rect x={cx - colW / 2 + 1} y={padT} width={colW - 2} height={plotH} rx="2" fill="#c6ee7c" fillOpacity="0.35" /> : null}
            {hasK ? <rect x={bx} y={barY} width={barW} height={barH} rx="1.5" fill={barFill} /> : null}
            {hasT ? <line x1={bx - 1} y1={yOf(w.threshold!)} x2={bx + barW + 1} y2={yOf(w.threshold!)} stroke="#13211a" strokeWidth="1.2" strokeDasharray="2 1.2" /> : null}
            {i % 2 === 0 || isOn ? (
              <text x={cx} y={H - 6} fontSize="6.3" textAnchor="middle" fontWeight={isOn ? "700" : "400"} fill={isOn ? "#1d3a2a" : "#56625a"}>
                {IUNG_SHORT[i]}
              </text>
            ) : null}
          </g>
        );
      })}
    </svg>
  );
}

function YearsAndTrendSvg({
  years,
  season,
  dayMonth,
  timeline,
}: {
  years: { year: number; value: number | null }[];
  season: number | null;
  dayMonth: string;
  timeline: ParcelReportSheet["timeline"];
}) {
  const W = 285;
  const H = 132;
  const yrList = years.slice(-10);
  const dryYears = yrList.filter((y) => y.value !== null && y.value <= -3).length;
  const cellGap = 3;
  const cellW = (W - cellGap * Math.max(0, yrList.length - 1)) / (yrList.length || 1);

  const tTop = 68;
  const tH = 44;
  const tMid = tTop + tH / 2;
  const slice = timeline.slice(-45);
  const barStep = W / Math.max(1, slice.length);
  const barW = Math.max(2, barStep - 1.5);

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="rs-pdf-svg" role="img" aria-label="10 lat i przebieg dzienny">
      <text x={0} y={11} fontSize="8.5" fontWeight="700" fill="#13211a">
        {`Ten dzień (${dayMonth || "—"}) w latach`}
      </text>
      <text x={W} y={11} fontSize="8" textAnchor="end" fill="#56625a">
        {yrList.length ? `Susza w ${dryYears} z ${yrList.length} lat` : "—"}
      </text>
      {yrList.map((yr, i) => {
        const x = i * (cellW + cellGap);
        const on = yr.year === season;
        return (
          <g key={yr.year}>
            <rect
              x={x}
              y={17}
              width={cellW}
              height={16}
              rx="2.5"
              fill={stageHex(yr.value)}
              stroke={on ? "#13211a" : "#ffffff"}
              strokeWidth={on ? "1.6" : "0.5"}
            />
            <text x={x + cellW / 2} y={42} fontSize="6.8" textAnchor="middle" fontWeight={on ? "700" : "400"} fill={on ? "#13211a" : "#56625a"}>
              {`'${String(yr.year).slice(2)}`}
            </text>
          </g>
        );
      })}

      <text x={0} y={60} fontSize="8.5" fontWeight="700" fill="#13211a">
        Stan wody dzień po dniu (−5…+5)
      </text>
      <line x1={0} y1={tMid} x2={W} y2={tMid} stroke="#9aa69e" strokeWidth="0.7" />
      {slice.map((d, i) => {
        if (d.value === null) return null;
        const mag = Math.max(0.4, Math.abs(d.value));
        const h = (mag / 5) * (tH / 2 - 2);
        const y = d.value >= 0 ? tMid - h : tMid;
        return (
          <rect
            key={d.date}
            x={i * barStep}
            y={y}
            width={barW}
            height={h}
            rx="1"
            fill={stageHex(d.value)}
            fillOpacity={d.forecast ? 0.45 : 0.92}
            stroke={d.forecast ? "#56625a" : "none"}
            strokeWidth={d.forecast ? "0.5" : undefined}
          />
        );
      })}
      {slice.length > 1 ? (
        <>
          <text x={0} y={127} fontSize="6.8" fill="#56625a">{dayNum(slice[0]!.date)}</text>
          <text x={W / 2} y={127} fontSize="6.8" textAnchor="middle" fill="#56625a">{dayNum(slice[Math.floor(slice.length / 2)]!.date)}</text>
          <text x={W} y={127} fontSize="6.8" textAnchor="end" fill="#56625a">{dayNum(slice[slice.length - 1]!.date)}</text>
        </>
      ) : null}
    </svg>
  );
}

function ParcelPage({ sheet, doc }: { sheet: ParcelReportSheet; doc: ReportDocument }) {
  const fillColor = stageHex(sheet.stage);
  return (
    <article className="rs-pdf-page">
      <header className="rs-pdf-head">
        <div className="rs-pdf-head__main">
          <div className="rs-pdf-brand">
            <Wordmark />
            <span className="rs-pdf-badge">Raport wody na działce · {sheet.analysisDateLabel}</span>
          </div>
          <h1 className="rs-pdf-h1">{sheet.headline}</h1>
          <p className="rs-pdf-sub">{sheet.subline}</p>
          <p className="rs-pdf-sub">
            Uprawa: <b>{sheet.cropLabel}</b> · Gleba: <b>{sheet.soilLabel}</b>
          </p>
        </div>
        {sheet.silhouette ? (
          <div className="rs-pdf-sil" aria-hidden="true">
            <svg viewBox={sheet.silhouette.viewBox} className="rs-pdf-sil__svg">
              {sheet.silhouette.paths.map((d, idx) => (
                <path key={idx} d={d} fill={fillColor} fillOpacity="0.48" fillRule="evenodd" stroke="#1d3a2a" strokeWidth="1.6" strokeLinejoin="round" />
              ))}
            </svg>
            <span>~{sheet.silhouette.widthM} × {sheet.silhouette.heightM} m</span>
          </div>
        ) : null}
      </header>

      {sheet.nonAgriMessage ? (
        <section className="rs-pdf-box rs-pdf-refusal">
          <h2 className="rs-pdf-h2">Działka poza zakresem analizy rolniczej</h2>
          <p>{sheet.nonAgriMessage}</p>
        </section>
      ) : (
        <>
          <section className="rs-pdf-status">
            <div className="rs-pdf-box rs-pdf-state">
              <div className="rs-pdf-state__top">
                <span className="rs-pdf-state__title">{sheet.stageName}</span>
                {sheet.risk ? (
                  <span className={`rs-pdf-pill rs-pdf-pill--${sheet.risk.level}`}>
                    Ryzyko {sheet.risk.level}
                  </span>
                ) : null}
                {sheet.confidence ? (
                  <span className="rs-pdf-pill">Pewność {sheet.confidence.level}</span>
                ) : null}
              </div>
              <div className="rs-pdf-scale" aria-hidden="true">
                {STEPS.map((step) => (
                  <span
                    key={step}
                    className={`rs-pdf-scale__cell${step === sheet.stage ? " is-on" : ""}`}
                    style={{ backgroundColor: STAGE_HEX[step] }}
                  />
                ))}
              </div>
              <div className="rs-pdf-scale__ends"><span>Susza (−5)</span><span>Optimum (0)</span><span>Nadmiar (+5)</span></div>
              {sheet.caption ? <p className="rs-pdf-caption">{sheet.caption}</p> : null}
              {sheet.risk?.veto && sheet.risk.vetoReason ? <p className="rs-pdf-veto">Weto: {sheet.risk.vetoReason}</p> : null}
              {sheet.componentsSummary ? <p className="rs-pdf-tiny">Wzór: {sheet.componentsSummary}</p> : null}
            </div>
            <div className="rs-pdf-kpis">
              {sheet.kpis.map((k) => (
                <div key={k.label} className="rs-pdf-box rs-pdf-kpi">
                  <span className="rs-pdf-kpi__lbl">{k.label}</span>
                  <strong className="rs-pdf-kpi__val">{k.value} <small>{k.unit}</small></strong>
                </div>
              ))}
            </div>
          </section>

          <section className="rs-pdf-charts">
            <div className="rs-pdf-box">
              <KbwSvgChart windows={sheet.kbwWindows} active={sheet.activeWindow} season={sheet.season} />
            </div>
            <div className="rs-pdf-box">
              <YearsAndTrendSvg years={sheet.years} season={sheet.season} dayMonth={sheet.dayMonthLabel} timeline={sheet.timeline} />
            </div>
          </section>

          {sheet.causes.length ? (
            <section className="rs-pdf-box">
              <div className="rs-pdf-sec-head">
                <h2 className="rs-pdf-h2">Przyczyny stanu wody</h2>
                {sheet.causesMethod ? <span className="rs-pdf-tiny">{sheet.causesMethod}</span> : null}
              </div>
              {sheet.causes.some((c) => c.pct !== null) ? (
                <div className="rs-pdf-cbar" aria-hidden="true">
                  {sheet.causes.map((c) => (
                    <span key={c.key} style={{ flexGrow: Math.max(c.pct ?? 1, 1), backgroundColor: CAUSE_HEX[c.key] }} />
                  ))}
                </div>
              ) : null}
              <div className="rs-pdf-causes">
                {sheet.causes.map((c) => (
                  <div key={c.key} className="rs-pdf-cause">
                    <span className="rs-pdf-dot" style={{ backgroundColor: CAUSE_HEX[c.key] }} />
                    <div>
                      <b>{c.label}{c.pct !== null ? ` (${c.pct}%)` : ""}</b>
                      <span> {c.note}</span>
                    </div>
                  </div>
                ))}
              </div>
            </section>
          ) : null}

          <section className="rs-pdf-two">
            <div className="rs-pdf-box">
              <h2 className="rs-pdf-h2">Działania retencyjne (top {sheet.measures.length})</h2>
              <table className="rs-pdf-tbl">
                <thead>
                  <tr><th>#</th><th>Działanie i efekt</th><th>Formalnie</th></tr>
                </thead>
                <tbody>
                  {sheet.measures.map((m) => (
                    <tr key={m.id} className={m.selected ? "is-picked" : undefined}>
                      <td>{m.selected ? "✓" : m.rank}</td>
                      <td><b>{m.title}</b><div className="rs-pdf-tiny">{m.effect}</div></td>
                      <td>{m.permit ? "Wody Polskie" : "Bez zgody"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {sheet.whatIfSummary ? <p className="rs-pdf-whatif">{sheet.whatIfSummary}</p> : null}
            </div>

            <div className="rs-pdf-box">
              <h2 className="rs-pdf-h2">Nawadnianie</h2>
              {sheet.irrigation ? (
                <>
                  <p className="rs-pdf-irr-title"><b>{sheet.irrigation.title}</b></p>
                  <p className="rs-pdf-tiny">{sheet.irrigation.text}</p>
                  {sheet.irrigation.meta ? <p className="rs-pdf-tiny">{sheet.irrigation.meta}</p> : null}
                  {sheet.irrigation.days.length ? (
                    <div className="rs-pdf-fc">
                      {sheet.irrigation.days.map((d) => (
                        <div key={d.date} className="rs-pdf-fc__day">
                          <b>{Math.round(d.valueMm)}</b>
                          <span>{dayNum(d.date)}</span>
                        </div>
                      ))}
                    </div>
                  ) : null}
                  {sheet.irrigation.strategies.length ? (
                    <table className="rs-pdf-tbl">
                      <thead><tr><th>Strategia (10 lat)</th><th>Strata</th><th>Woda</th></tr></thead>
                      <tbody>
                        {sheet.irrigation.strategies.map((s) => (
                          <tr key={s.label} className={s.active ? "is-picked" : undefined}>
                            <td>{s.label}</td>
                            <td>{s.yieldLossPct.toFixed(1)}%</td>
                            <td>{Math.round(s.waterMm)} mm</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  ) : null}
                </>
              ) : (
                <p className="rs-pdf-tiny">Brak danych nawadniania dla tej daty.</p>
              )}
            </div>
          </section>
        </>
      )}

      <footer className="rs-pdf-foot">
        <div className="rs-pdf-foot__sources">
          <b>Źródła:</b>{" "}
          {sheet.sources.length
            ? sheet.sources.map((s) => `${s.name} (${s.resolution})`).join(" · ")
            : "GUGiK ULDK, IUNG-PIB WMS, Open-Meteo ERA5/ECMWF"}
        </div>
        <div className="rs-pdf-foot__meta">
          <span>Analiza oparta wyłącznie na danych przedmiotowych działki (bez danych osobowych). Wygenerowano: {doc.generatedAt}.</span>
          <b>Strona {sheet.pageNumber} / {sheet.totalPages}</b>
        </div>
      </footer>
    </article>
  );
}

/** Static A4 multi-parcel print report built from ReportDocumentBuilder (hidden on screen, shown in @media print). */
export function PrintReport({ doc }: { doc: ReportDocument }) {
  if (!doc.sheets.length) return null;
  return (
    <div className="rs-print-doc" aria-hidden="true">
      {doc.sheets.map((sheet) => (
        <ParcelPage key={sheet.parcelId} sheet={sheet} doc={doc} />
      ))}
    </div>
  );
}
