import { Link } from "react-router-dom";
import { BAL, signed, Wordmark } from "../ds";
import { IUNG_PROGI } from "../lib/iung";

const RULES: [number, string][] = [
  [-5, "KBW z 60 dni niższy od progu IUNG o ponad 60 mm."], [-4, "KBW poniżej progu IUNG."], [-3, "KBW mniej niż 25 mm nad progiem."],
  [-2, "KBW poniżej 60% progu."], [-1, "KBW poniżej 30% progu."], [0, "KBW od 30% progu do +40 mm."], [1, "KBW od +40 do +80 mm."],
  [2, "KBW od +80 do +130 mm."], [3, "KBW od +130 do +200 mm."], [4, "KBW od +200 do +280 mm."], [5, "KBW powyżej +280 mm."],
];

/** Data that feeds the water state, the zones and the irrigation plan (backend clients and ML training data). */
const SOURCES: [string, string, string][] = [
  ["Gleba", "IUNG-PIB, System Monitoringu Suszy Rolniczej", "kategoria gleby I–IV i progi suszy dla 14 upraw"],
  ["Gleba", "ISRIC SoilGrids v2.0", "skład gleby i ilość wody, którą gleba zatrzyma"],
  ["Pogoda", "Open-Meteo: reanaliza ERA5 i ERA5-Land", "opad i parowanie dzień po dniu, 10 lat wstecz"],
  ["Prognoza", "ECMWF IFS, 51 wariantów prognozy", "opad i parowanie na 14 dni, szansa podlewania"],
  ["Pogoda", "IMGW-PIB, stacje telemetryczne", "kontrola bieżących danych pogodowych z najbliższej stacji"],
  ["Pogoda", "NASA POWER", "zapas, gdy Open-Meteo nie odpowiada"],
  ["Satelita", "Copernicus Sentinel-2", "wilgotność łanu (NDMI) z wagą zależną od fazy uprawy: po siewie, po zbiorze i poza sezonem satelita nie wpływa na wynik"],
  ["Teren", "GUGiK NMT 1 m, zapas Copernicus DEM 30 m", "spadki, spływ i strefy wilgotne i suche"],
  ["Woda", "GUGiK BDOT10k", "rowy melioracyjne, cieki i zbiorniki"],
  ["Działka", "GUGiK ULDK, ortofotomapa i KIEG", "granica, powierzchnia i mapa działki"],
];
const MODELS: [string, string][] = [
  ["KBW według IUNG-PIB", "susza w 14 oknach po 60 dni względem progu uprawy i gleby"],
  ["Bilans wody FAO-56", "woda w strefie korzeni dzień po dniu, z fazą rozwoju uprawy"],
  ["SCS-CN i FAO-33", "spływ po deszczu i strata plonu przy stresie wodnym"],
  ["HydroFusion-PL", "model uczenia maszynowego, który szacuje wilgotność roślin w dni z chmurami nad działką"],
  ["Agent RL", "decyzja podlać czy poczekać i plan dawek, sprawdzony na 10 sezonach działki"],
];

export function Method() {
  return (
    <div className="rs-analysis">
      <header className="rs-ahead"><Link to="/" aria-label="Wróć do mapy"><Wordmark /></Link><Link className="ar-btn ar-btn--light" to="/">Wróć do mapy</Link></header>
      <main className="rs-doc">
        <h1 className="ar-display">Jak <em>liczymy</em></h1>
        <section className="ar-card">
          <h2 className="ar-card__title">Susza według metody IUNG-PIB</h2>
          <p>Rusałka liczy klimatyczny bilans wodny KBW = opad − ewapotranspiracja ET₀ dla punktu w środku działki. Dane dzienne pochodzą z Open-Meteo (reanaliza ERA5 i prognoza).</p>
          <p>KBW liczymy w 14 oknach po 60 dni, jak System Monitoringu Suszy Rolniczej IUNG-PIB: od 21.III–20.V do 1.VIII–30.IX. Wynik porównujemy z progiem suszy dla uprawy i kategorii gleby.</p>
          <p>Progi pochodzą z tabeli IUNG-PIB ({IUNG_PROGI.basis}). Tabelę pobraliśmy {IUNG_PROGI.retrieved}. Kategorię gleby I–IV podaje warstwa IUNG <code>Susza:katgl_landuse2024</code>.</p>
        </section>
        <section className="ar-card">
          <h2 className="ar-card__title">Stan wody od −5 do +5</h2>
          <p>Zero to optimum. Strona ujemna to susza, strona dodatnia to nadmiar wody. Stopień wynika z KBW z 60 dni przed datą analizy i progu IUNG.</p>
          <table><tbody>{RULES.map(([v, t]) => <tr key={v}><td><b>{signed(v)}</b></td><td>{BAL[String(v)]}</td><td>{t}</td></tr>)}</tbody></table>
          <p>Tabela pokazuje regułę oceny w przeglądarce. Gdy działa serwer, stan wody pochodzi z dobowego bilansu wody w strefie korzeni (FAO-56) połączonego z KBW, satelitą i terenem.</p>
        </section>
        <section className="ar-card">
          <h2 className="ar-card__title">Z czego składa się wynik</h2>
          <p>Stan wody, strefy na działce i plan podlewania łączą dane publiczne z kilku instytucji. Każda analiza pokazuje w sekcji źródeł, które z nich zadziałały i kiedy je pobraliśmy.</p>
          <table>
            <tbody>
              {SOURCES.map(([what, who, use]) => <tr key={who}><td><b>{what}</b></td><td>{who}</td><td>{use}</td></tr>)}
            </tbody>
          </table>
          <p>Z tych danych liczą modele:</p>
          <ul className="rs-list">
            {MODELS.map(([name, use]) => <li key={name}><b>{name}</b>: {use}</li>)}
          </ul>
        </section>
        <section className="ar-card">
          <h2 className="ar-card__title">Ograniczenia</h2>
          <ul className="rs-list">
            <li>Pogoda pochodzi z siatki ok. 9 km, a nie z pomiaru na działce. Lokalna burza może się różnić, dlatego opad z ostatnich 7 dni możesz wpisać z własnego deszczomierza.</li>
            <li>IUNG liczy ewapotranspirację metodą Penmana, a Open-Meteo metodą FAO-56 Penman-Monteith. Wyniki mogą się różnić o kilka procent.</li>
            <li>Kategoria gleby pochodzi z jednego punktu w środku działki.</li>
            <li>Mapy drenowań nie są publiczne: drenaż ocenia sieć rowów melioracyjnych, a pewną odpowiedź podajesz sam przy ocenie działki. Poziomu wód gruntowych ani wykonanych zabiegów Rusałka nie zna; możliwy podsiąk sygnalizuje przy glebach organicznych, obniżeniach terenu i blisko cieków.</li>
            <li>Każda zmiana urządzeń wodnych, np. zastawki na rowie, wymaga zgody Wód Polskich.</li>
          </ul>
        </section>
      </main>
    </div>
  );
}
