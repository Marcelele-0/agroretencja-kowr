// Pobiera tabelę progów suszy IUNG-PIB (SMSR) i zapisuje ją jako src/data/iung-progi.json.
// Użycie: pnpm iung:progi [ścieżka-do-zapisanego-html]
import { readFileSync, writeFileSync } from "node:fs";

const URL = "https://susza.iung.pulawy.pl/progi/";
const OUT = new globalThis.URL("../src/data/iung-progi.json", import.meta.url);

const html = process.argv[2] ? readFileSync(process.argv[2], "utf8") : await (await fetch(URL)).text();
const table = html.slice(html.indexOf("<table"), html.indexOf("</table>"));
const strip = (s) => s.replace(/<[^>]*>/g, "").replace(/&nbsp;/g, " ").replace(/\s+/g, " ").trim();

const periods = [...table.matchAll(/<th title="(\d{2})"[^>]*>([^<]*)<\/th>/g)].map((m) => m[2].replace(/\s*-\s*/, "–").trim());
if (periods.length !== 14) throw new Error(`Oczekiwano 14 okresów, jest ${periods.length}`);

const crops = {};
let crop = null;
for (const row of table.split(/<tr[\s>]/).slice(1)) {
  const cells = [...row.matchAll(/<td([^>]*)>([\s\S]*?)<\/td>/g)].map((m) => ({ attrs: m[1], text: strip(m[2]) }));
  if (cells.length === 16) { crop = cells[0].text; cells.shift(); }
  if (cells.length !== 15 || !crop) continue;
  const cat = cells[0].text;
  if (!/^(I|II|III|IV)$/.test(cat)) continue;
  const values = cells.slice(1).map((c) => (c.text === "x" ? "x" : c.text === "-" ? "-" : Number(c.text.replace(",", "."))));
  if (values.some((v) => typeof v === "number" && !Number.isFinite(v))) throw new Error(`Zła liczba: ${crop} ${cat}`);
  (crops[crop] ??= {})[cat] = values;
}

const out = {
  source: URL,
  retrieved: new Date().toISOString().slice(0, 10),
  basis: "Rozporządzenie Ministra Rolnictwa i Rozwoju Wsi z dnia 11 kwietnia 2019 r. (progi KBW, jednakowe dla wszystkich województw)",
  meaning: { number: "krytyczna wartość KBW [mm] dla okresu 60 dni", x: "brak strat plonu w tym okresie", "-": "uprawa nie występuje na tej kategorii gleby" },
  periods,
  crops,
};
writeFileSync(OUT, JSON.stringify(out, null, 1) + "\n");
console.log(`Zapisano ${Object.keys(crops).length} upraw, ${periods.length} okresów -> ${OUT.pathname}`);
