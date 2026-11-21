# AgroRetencja KOWR — Blueprint Rozwiązania

> **Wyzwanie:** HackNation 2026 — KOWR: *Zatrzymaj wodę, chroń plony – Inteligentne wsparcie gospodarowania wodą w rolnictwie*  
> **Standard dokumentacji:** ASD-STE100 (krótkie zdania, strona czynna, zero zbędnych opisów)  
> **Status IP (§ 5 Regulaminu KOWR):** Czysty kod pisany od zera wyłącznie pod zadanie konkursowe KOWR.  
> **Konsultacje z mentorami (21.11.2026):** Ustalenia i ich wpływ na priorytety opisuje [sekcja 8](#8-ustalenia-z-konsultacji-z-mentorami-kowr-21112026).

---

## 1. Nazwa Produktu, Pitch Hook i 3 Wyróżniki WOW

### Nazwa produktu
**AgroRetencja KOWR** (*Cyfrowy Asystent Retencji i Bilansu Wodnego Działki Rolnej*)

### 1-zdaniowy Pitch Hook
**AgroRetencja KOWR** zamienia w 10–18 sekund numer dowolnej działki ewidencyjnej w Polsce, wybraną uprawę i dowolną datę z ostatnich 10 lat (2016–2026) w audytowalną diagnozę przyczyn niedoboru wody, mapę mikro-spływu z NMT 1 m oraz policzalny plan retencji *„zatrzymaj wodę tam, gdzie spadła”*.

### 3 Wyróżniki WOW
1. **Mikrotopografia NMT 1 m na żywo + Symulator „Co jeśli” z modelem plonowania FAO-33:**
   System wycina dla granic działki numeryczny model terenu **GUGiK NMT 1 m (WCS)**, liczy spadki, wektory spływu D8, wskaźnik wilgotności topograficznej (**TWI**) oraz naturalne mikrozagłębienia. Rolnik jednym przełącznikiem testuje zabiegi (międzyplony, mulcz, pas buforowy, piętrzenie w rowie) i widzi zysk na warunkach **najsuchszego roku z 10 lat tej działki**: dodatkowe dni rezerwy wodnej, zatrzymane milimetry wody oraz redukcję straty plonu (**FAO-33**).
2. **Dwa lekkie, wytrenowane na danych z Polski modele ML (`HydroFusion-PL` + `FAO-RL-Irrigator`):**
   - **`HydroFusion-PL` (Kroki 1–2):** Rekonstruuje wskaźnik uwilgotnienia $\widehat{\text{NDMI}}$ pod chmurami (~55% dni w Polsce) z 90% przedziałem ufności (**Conformal Prediction**) oraz rozbija przyczyny niedoboru na 4 grupy czynników KOWR przez dokładny **TreeSHAP** (`< 25 ms` na CPU).
   - **`FAO-RL-Irrigator` (Krok 4, opcjonalny po retencji):** Uniwersalna polityka **PPO** uwarunkowana na `(Kc, Ky, TAW)`, ucząca się na dobowym symulatorze FAO-56/33 oszczędzać wodę przed prognozowanym deszczem i porównywana uczciwie w tabeli 10-letniej z regułą progową FAO-56 (`< 50 ms` na CPU).
3. **Architektura *Explainability by Design*: `WaterScore` + `WaterRisk (Veto)` + `Pewność Danych (AoA)`:**
   System oddziela percepcję ML od deterministycznego bilansu wodnego (**FAO-56 + IUNG-PIB KBW**). Krytyczny deficyt lub gwałtowny spływ na glebie lekkiej uruchamia **absolutne veto** (`WaterRisk`), a osobna odznaka **Pewność Danych (AoA)** komunikuje niepewność i braki danych bez pozornej precyzji.

---

## 2. Ostateczna Koncepcja — Synteza Zasobów i Zasady Architektoniczne

Rozwiązanie łączy pięć pryncypiów projektowych w jeden spójny potok zgodny z kolejnością wymaganą przez KOWR:

```text
[1. WEJŚCIE ROLNIKA]      TERYT / Klik na mapie (ULDK)  +  Uprawa (FAO-56/33 Kc, Ky, p)  +  Data t (2016–2026)
                                                   │
                                                   ▼
[2. POBRANIE 5 KATEGORII] Meteo 10 lat (Open-Meteo/IMGW) │ Satelita (S2 AWS STAC COG) │ Gleba (SoilGrids + IUNG)
                          NMT 1 m (GUGiK WCS + ULDK)     │ Hydro/Susza (Geoportal BDOT10k WMS + EDO /api/wms)
                                                   │
                                                   ▼
[3. WARSTWA PERCEPCJI]    Wskaźniki EO (NDVI/NDMI/SMAI) + Mikrotopografia NMT 1 m (Spadek, Spływ D8, TWI)
                          + HydroFusion-PL (rekonstrukcja pod chmurami + TreeSHAP + AoA)
                                                   │
                                                   ▼
[4. SILNIK FIZYCZNY]      Dobowy Bilans Wodny FAO-56 + IUNG-PIB (KBW_60, TAW 4 kat., FAO-33 strata plonu)
                          Ocena dwuosiowa: WaterScore (0–100) + WaterRisk (VETO) + DataConfidence (AoA)
                                                   │
                                                   ▼
[5. DECYZJA I RAPORT]     1. Ocena (bieżąca + 10 lat) → 2. Diagnoza przyczyn → 3. Ranking retencji + „Co jeśli”
                          → 4. Nawadnianie (FAO baseline vs PPO RL) → 5. Audytowalny Raport PDF (2 strony, RODO)
```

### Pryncypia projektowe i dyscyplina zakresu

| Zasada architektoniczna | Zastosowanie w AgroRetencja KOWR | Czego świadomie unikamy |
| :--- | :--- | :--- |
| **Sekwencja 5 kroków KOWR** | Obowiązkowa kolejność: ocena 10 lat → przyczyny → retencja → nawadnianie → raport PDF. | Generowania rekomendacji bez policzenia bilansu działki; statycznych szablonów. |
| **Warstwa Dowodu zamiast mapy GIS** | Fuzja 5 źródeł przekształcona w datowany łańcuch *Stan X → Przyczyna Y → Działanie Z* z audytem. | Surowego portalu GIS z wieloma warstwami bez wyjaśnienia decyzji. |
| **Oś czasu działki + audytowalny PDF** | `TERYT → oś 10 lat (2016–2026) → 2-stronicowy PDF` z datami, metodą, niepewnością i higieną RODO. | Przetwarzania danych osobowych rolnika; pozornej precyzji przy grubszej skali danych. |
| **Ranking priorytetów działań** | Lista działań posortowana według przyrostu rezerwy wodnej (mm / dni / % uratowanego plonu). | Rozbudowanych modułów poza gospodarką wodną; płatnych warstw i licencji komercyjnych. |
| **Explainability by Design + AoA** | Rozdzielenie ML od fizyki FAO-56/33; `WaterScore` + `WaterRisk (Veto)`; osobna niepewność *Area of Applicability*. | Czarnej skrzynki bez wyjaśnienia; ukrywania braków danych w średniej punktowej. |

---

## 3. Fizyka Bilansu, Ocena Dwuosiowa i Dwa Lekkie Modele ML

### 3.1. Deterministyczny silnik fizyczny (FAO-56 + IUNG-PIB + FAO-33)
Silnik fizyczny działa w 100% deterministycznie na CPU (`< 50 ms` dla pełnych 10 lat) bez udziału LLM:
- **Zapas wody dostępnej w glebie ($\text{TAW}$) wg 4 kategorii IUNG-PIB** (powiązanych z danymi `ISRIC SoilGrids`):
  - Kat. I: `< 127,5 mm` | Kat. II: `127,5–170 mm` | Kat. III: `170–202,5 mm` | Kat. IV: `> 202,5 mm`.
- **Woda łatwo dostępna:** $\text{RAW} = p \cdot \text{TAW}$ (parametry uprawowe $K_c, K_y, p$ z tabel FAO-56/33, konsultowane z mentorami KOWR: dr Bolewski, dr hab. Gozdowski).
- **Klimatyczny Bilans Wodny (60 dni, IUNG):** $\text{KBW}_{60}(t) = \sum_{d=t-59}^{t}(P_d - ET_{0,d})$. Obok wartości ciągłej silnik liczy 14 stałych okien SMSR IUNG-PIB dla roku daty $t$ (sekcja 8.2).
- **Dobowy krok bilansu w strefie korzeniowej** $D_{r,d} \in [0, \text{TAW}]$:
  $$K_{s,d} = \begin{cases} 1 & D_{r,d-1} \le \text{RAW} \\ \max\!\left(0,\; \frac{\text{TAW} - D_{r,d-1}}{\text{TAW} - \text{RAW}}\right) & D_{r,d-1} > \text{RAW} \end{cases}, \qquad ET_{c,d} = K_c(d)\cdot ET_{0,d}, \qquad ET_{a,d} = K_{s,d}\cdot ET_{c,d}$$
  $$D_{r,d} = \text{clip}\!\Big(D_{r,d-1} + ET_{a,d} - (P_d - RO_d) - I_d,\; 0,\; \text{TAW}\Big), \qquad DP_d = \max\!\big(0,\; P_d + I_d - RO_d - ET_{a,d} - D_{r,d-1}\big)$$
  gdzie $RO_d$ oznacza odpływ powierzchniowy (funkcja opadu nawalnego i spadku $\beta$ z NMT 1 m), $I_d$ to dawka nawadniania, a $DP_d$ to przesiąk wgłębny (strata wody).
- **Wzór odpowiedzi plonu (FAO-33 — zasila symulator „Co jeśli” i środowisko RL):**
  $$1 - \frac{Y_a}{Y_{\max}} = K_y \cdot \left(1 - \frac{\sum ET_{a,d}}{\sum ET_{c,d}}\right)$$
- **Mikrotopografia NMT 1 m i wskaźniki EO:**
  $$\text{TWI} = \ln\!\left(\frac{a}{\tan\beta + \varepsilon}\right), \qquad \text{NDMI} = \frac{B8A - B11}{B8A + B11}, \qquad \text{SMAI} = \frac{\text{NDMI} - \mu_{\text{DOY}}}{\sigma_{\text{DOY}}}$$

### 3.2. Ocena dwuosiowa (`WaterScore` + `WaterRisk Veto`) i Niepewność (`DataConfidence`)
$$\text{WaterScore} = 0.40 \cdot S_{\text{bilans}} + 0.30 \cdot S_{\text{sat/ML}} + 0.15 \cdot S_{\text{gleba}} + 0.15 \cdot S_{\text{topo}}$$
Gdy odczyt satelitarny i rekonstrukcja ML są niedostępne lub poza AoA, waga $0.30$ jest jawnie przeliczana proporcjonalnie na pozostałe trzy składowe.

**Reguły Absolutnego Weta (`WaterRisk` — nigdy nie uśredniane w `WaterScore`):**
1. KBW ostatniego zakończonego okna SMSR (koniec okna $\le t$) poniżej progu suszy IUNG dla trójki `(uprawa, kategoria gleby, okno)` $\rightarrow$ **KRYTYCZNE** (tabela progów: sekcja 8.2).
2. $D_r > 0.85 \cdot \text{TAW}$ w fazie wrażliwej uprawy $\rightarrow$ **KRYTYCZNE**.
3. Średni spadek $\beta > 5^\circ$ na glebie lekkiej kat. I bez pasa buforowego $\rightarrow$ min. **WYSOKIE**.

**Niepewność `DataConfidence (AoA)` wyświetlana obok wyniku (4 wymiary jawności):**
1. **Skala przestrzenna vs powierzchnia działki:** ostrzeżenie dla działek `< 0,2 ha` (rozdzielczość Sentinel-2 10 m) oraz informacja o regionalnej skali `SoilGrids` (250 m) i `Open-Meteo` (~9 km).
2. **Świeżość satelity i przedział ufności:** dokładna data ostatniego bezchmurnego przelotu `Sentinel-2`; przy chmurach jawny 90% przedział ufności $[\hat{y}_{0.05}, \hat{y}_{0.95}]$.
3. **Area of Applicability ($DI$):** przy $DI > \tau_{95}$ model ML automatycznie wyłącza się i przekazuje ocenę czystemu bilansowi fizycznemu.
4. **Spójność źródeł i braki domenowe:** rozbieżność między suszą meteo a wysokim NDVI generuje ostrzeżenie o możliwym lokalnym nawadnianiu lub podsiąku gruntowym; raport wymienia czynniki nieuwzględnione (drenaż rurkowy, zwierciadło wód gruntowych).

---

### 3.3. Trening ML #1: `HydroFusion-PL` (Kroki 1–2 — Chmury i Przyczyny)

- **Architektura:** Zespół multi-kwantylowy **LightGBM** ($\tau \in \{0.05, 0.50, 0.95\}$) + **Split Conformal Prediction (90%)** + natywny C++ **TreeSHAP** (`pred_contrib=True`) + detektor **Area of Applicability ($DI$, Meyer & Pebesma 2021)**.
- **Dlaczego nie ciężki Deep Learning ani LLM:**
  - *3D U-Net / ConvLSTM:* pobranie terabajtów pełnych kafelków z 10 lat przekroczy budżet 24 h i zagrozi limitowi `≤ 30 s` na inferencji.
  - *LLM jako silnik decyzyjny:* wysokie ryzyko halucynacji liczb i złamania zakazu pozornej precyzji.
  - `HydroFusion-PL` pobiera próbki przez COG Range Reads, uczy się na GPU/CPU w **10–20 min**, waży `< 15 MB` i wykonuje predykcję wraz z TreeSHAP w **< 25 ms na CPU**.
- **Cel predykcji i dwie role:**
  1. *Cloud-Gap Nowcasting:* gdy na dacie $t$ zachmurzenie `SCL > 30%`, rekonstruuje $\widehat{\text{NDMI}}(t)$ z 90% przedziałem ufności.
  2. *Dekompozycja 4 przyczyn KOWR:* agreguje wartości TreeSHAP z 18 cech do 4 grup procentowych: **Klimat/ET₀**, **Gleba/AWC**, **Spływ NMT**, **Uprawa/fenologia**.
- **Darmowy zbiór treningowy z Polski (pobierany w ~30–45 min):**
  - 300–600 stratyfikowanych punktów rolniczych (16 województw × 4 kategorie gleby IUNG × gradient spadku NMT) × 10 lat (2016–2026, marzec–październik) = **~35 000–65 000** bezchmurnych okien `3×3 px` (`SCL in {4, 5}`) pobranych przez `Earth Search STAC + HTTP Range` + `Open-Meteo` + `SoilGrids` + `GUGiK NMT`.
- **18 cech wejściowych:**
  - *Klimat (7):* $P_{15}, P_{30}, P_{60}$, $ET_{0,30}$, $\text{KBW}_{30}$, $\text{KBW}_{60}$, długość ciągu bezopadowego $\text{CDD}$, $\text{VPD}_{15}$.
  - *Gleba (4):* $\text{AWC}_{0-60\text{cm}}$, udział piasku `[%]`, węgiel organiczny $\text{SOC}$ `[g/kg]`, kategoria IUNG `(I–IV)`.
  - *Topografia NMT (3):* spadek $\beta$, wskaźnik $\text{TWI}$, ekspozycja południowa.
  - *Stan łanu i czas (4):* ostatnie bezchmurne $\text{NDVI}_{t-\Delta t}$, $\text{NDMI}_{t-\Delta t}$, opóźnienie $\Delta t$, kod fazy fenologicznej `DOY` + $K_c$.
- **Walidacja przestrzenno-czasowa (do Sekcji #9 README):**
  - Wyłączone z treningu **2 województwa holdout** (wielkopolskie, lubelskie) oraz **2 lata ekstremalnych susz (2018, 2023)**.
  - Metryki docelowe: `RMSE ≤ 0.048`, `MAE ≤ 0.035`, `R² ≥ 0.78`, `PICP 90% = 89%–91%`, zgodność klasyfikacji stresu z IUNG `≥ 85%`.

### 3.4. Trening ML #2: `FAO-RL-Irrigator` (Krok 4 — Nawadnianie sekwencyjne)

- **Zakres i granice stosowania RL:**
  - *Tak (Krok 4 — Nawadnianie):* klasyczny problem sekwencyjny dzień-po-dniu pod niepewną pogodę i ograniczony budżet wody.
  - *Nie (Krok 1 — Ocena suszy):* ocena musi pozostać czystym deterministycznym obliczeniem porównywanym z danymi referencyjnymi jury.
  - *Etap 2 rozwoju (Slajd 9 — poza 24h MVP):* sterowanie zastawkami na rowach melioracyjnych (w otwartych danych brak telemetrycznych stanów wody i geometrii zastawek; włączymy dopiero po integracji czujników IoT dla KOWR).
- **Architektura i specyfikacja MDP:**
  - **Model:** Pojedyncza uniwersalna polityka **PPO (`stable-baselines3`)** z siecią **MLP `[64, 64]`** uwarunkowaną na parametrach uprawy i gleby `(Kc, Ky, TAW)`, co zastępuje utrzymywanie 24 osobnych modeli.
  - **Stan (obserwacja, ~13 cech):** dzień sezonu i faza uprawy, deficyt jako `% TAW`, opad i $ET_0$ z ostatnich 7 dni, prognoza opadu na 3–7 dni, zużyta część budżetu wody, parametry `(Kc, Ky, TAW)`.
  - **Akcja:** dawka dobowa $I_d \in \{0, 10, 20, 30\}\text{ mm}$.
  - **Nagroda:** na końcu sezonu względny plon FAO-33 $(Y_a / Y_{\max})$, a codziennie kara za zużycie wody i przesiąk wgłębny ($-c_w I_d - c_{dp} DP_d$) — agent uczy się nie podlewać tuż przed deszczem.
- **Dane, brak *look-ahead bias* i szybki trening:**
  - **Dane (pobranie ~30 s):** 60 reprezentatywnych punktów Polski × 10 lat dziennych danych `Open-Meteo` (podział czasowy: **2016–2023 trening**, **2024–2025 test**).
  - **Zapobieganie wyciekowi przyszłości:** dla daty dzisiejszej agent otrzymuje realną prognozę 7-dniową z `Open-Meteo Forecast`; dla historycznych dat testowych prognoza ma nakładany skalibrowany szum stochastyczny.
  - **Trening i eksport produkcyjny:** `1M` kroków na 16 równoległych środowiskach CPU trwa **~2–5 min** (~30–40 min ze strojeniem). Wagi aktora eksportujemy do **`.npz` (czysty NumPy)**, dzięki czemu serwer produkcyjny działa bez PyTorcha i SB3 (`< 50 ms`).
- **Uczciwy baseline i warunek włączenia:** Na 10-letniej historii działki pokazujemy tabelę porównującą `Bez nawadniania` vs `Reguła progowa FAO-56` vs `Agent RL (PPO)` (średnia strata plonu `%`, woda `mm/sezon`, przesiąk `mm`, `WUE`). Jeśli PPO nie wygrywa oszczędnością wody lub WUE na latach testowych 2024–2025, Krok 4 działa jawnie na deterministycznej regule FAO-56.

---

## 4. UX „Asystent Decyzji z Mapą w Tle” i Raport PDF

Interfejs działa w przeglądarkach Windows bez wtyczek. Układ dzieli ekran na **60% Karty Decyzji** po lewej i **40% Mapy Dowodowej** po prawej.

1. **Pasek wejściowy (3 kliknięcia):** Identyfikator `TERYT` / klik na mapie + wybór uprawy + wybór daty z 10 lat (skróty: *Dziś* | *Szczyt wegetacji* | *Najsuchszy dzień 10-lecia*). Link otwiera od razu mapę Polski z granicami działek i polem wyszukiwania. Data poza sezonem 21.III–30.IX uruchamia widok „poza sezonem” (sekcja 8.3).
2. **Karta Decyzji (5 kroków KOWR):**
   - *Krok 1:* `WaterScore (0–100)` + `WaterRisk (Veto)` + osobna `Pewność Danych (AoA)` + wykres 10 lat (2016–2026).
   - *Krok 2:* Wykres 4 przyczyn niedoboru wody (`TreeSHAP` + wyjaśnienie słowne).
   - *Krok 3:* Ranking retencji (*NWRM* + agrotechnika) + **Symulator „Co jeśli”** przeliczający w czasie rzeczywistym przyrost rezerwy wodnej (dni, mm) i spadek straty plonu (**FAO-33**) w najsuchszym roku dekady. Ranking rozdziela działania „bez urządzeń wodnych” i działania „wymaga zgody Wód Polskich” (sekcja 8.1, pkt 5).
   - *Krok 4:* Karta nawadniania (*Czy podlewać w ciągu 7 dni / ile mm / okno*) + 10-letnia tabela porównania strategii (`Bez` vs `Reguła FAO` vs `Agent RL`).
3. **Rola mapy (3 czytelne widoki bez żargonu GIS):**
   - *Gdzie ucieka woda* (`NMT 1 m`: cieniowanie, strzałki spływu D8, niebieskie strefy wysokiego TWI),
   - *Kondycja i wilgotność uprawy* (`Sentinel-2 NDMI/NDVI`),
   - *Plan działań na działce* (pinezki lokalizacji pasów buforowych / niecek retencyjnych).
4. **2-stronicowy audytowalny raport PDF:**
   - *Strona 1:* Diagnoza, mapa NMT z planem działań, tabela rankingu i efekt „Co jeśli”.
   - *Strona 2:* Tabela 5 źródeł (`LIVE` vs `PRE-FETCHED` z datami), licencje, poziom niepewności (`AoA`, szerokość przedziału konformalnego, braki danych) i klauzula RODO.

---

## 5. Architektura Techniczna ≤ 30 s, 5 Darmowych API i Skalowanie

### 5.1. Budżet latencji dla losowej działki (`≤ 30 s`, cel `10–18 s`)

| Etap potoku | Czas typowy | Timeout | Fallback |
| :--- | :---: | :---: | :--- |
| 1. Geometria działki (`ULDK GUGiK`) | `0,8 s` | `2,5 s` | Lokalny indeks `0_dzialki.parquet` |
| 2. Równoległe pobranie 5 kategorii (`asyncio.gather`) | `6,5 s` | `8,0 s` | Niezależne timeouty per API: |
| ↳ `Open-Meteo 10y + Forecast` / `IMGW` | *(2,2 s)* | *(5,0 s)* | Lokalna siatka klimatyczna |
| ↳ `Sentinel-2 Earth Search STAC + COG Range` | *(4,8 s)* | *(8,0 s)* | Rekonstrukcja `HydroFusion-PL` |
| ↳ `GUGiK NMT 1 m WCS` | *(3,5 s)* | *(6,0 s)* | Zbuforowany `Copernicus DEM 30 m` |
| ↳ `ISRIC SoilGrids v2` + progi `IUNG-PIB` | *(1,5 s)* | *(3,5 s)* | Lokalna siatka Polski + cache SQLite |
| ↳ `Geoportal BDOT10k WMS` + `EDO /api/wms` | *(2,0 s)* | *(4,0 s)* | Lokalna sieć cieków + flaga braku tła EDO |
| 3. Przetwarzanie NMT + Bilans FAO-56/33 (10 lat) | `1,5 s` | `3,0 s` | — |
| 4. Inferencja CPU `HydroFusion-PL` + `PPO .npz` | `< 0,08 s` | `0,2 s` | Czysty bilans fizyczny / reguła FAO-56 |
| 5. Render JSON + PDF (`WeasyPrint`) | `1,5 s` | `3,0 s` | — |
| **ŁĄCZNY CZAS ANALIZY** | **~10,5 s** | **≤ 17,7 s** | **Zapas > 12 s pod twardym limitem 30 s** |

*Uwaga z weryfikacji API (21.11.2026):* Usługi `wody.isok.gov.pl/wss/INSPIRE` oraz `Geoportal WFS/SiecWodna` zwracają `HTTP 401`, a `drought.emergency.copernicus.eu/data/wms-service` zwraca HTML zamiast XML. Wykorzystujemy działające publiczne endpointy `Geoportal BDOT10k WMS` (`0,31 s`) i `EDO /api/wms` (`0,26 s`).

### 5.2. Lekki runtime i skalowanie na całą Polskę (`1,4 mln ha`)
- **Runtime CPU (brak PyTorch/SB3/SHAP):** FastAPI, Pydantic v2, HTTPX, NumPy, SciPy, Rasterio, LightGBM (`pred_contrib=True`), WeasyPrint.
- **Skalowanie (Pkt 5.5 KOWR):** Cache warstwowy `(id_dzialki, data)` w PostGIS/Redis (`< 0,3 s`), współdzielenie kafli S2 MGRS i siatki meteo na poziomie gminy (`-98%` zapytań), nocna aktualizacja dobowych przelotów. Koszt pojedynczego serwera obsługującego `> 50 tys.` analiz/dobę szacowany na `~350–500 zł/mies.`.

---

## 6. Bezwzględny Plan 24h (MVP → WOW → Cut List)

| Strumień | H0–H8 (MVP i Treningi) | H8–H18 (WOW i Integracja) | H18–H24 (Testy i Oddanie) |
| :--- | :--- | :--- | :--- |
| **1. Rdzeń i Dane** | `hydro_core`: FAO-56/33, IUNG TAW, NMT TWI/D8, 6 klientów async `asyncio`. | Cache SQLite, fallbacki API, benchmark 20 losowych działek PL (`≤ 30 s`). | Zamrożenie cache testowego, testy E2E `pytest`. |
| **2. ML (`HydroFusion` + `PPO`)** | Pobranie Open-Meteo (30 s) + trening `PPO` (5 min) + pobranie S2 COG (35 min) + trening `HydroFusion` (15 min). | Eksport wag `.txt` / `.npz`, integracja `TreeSHAP`, `Conformal 90%`, `AoA`. | Wykresy walidacji holdout do Sekcji #9 README. |
| **3. API i PDF** | FastAPI (`/api/analyze`, `/api/what-if`) na wspólnym `schemas.py`. | Generator 2-stronicowego PDF (`WeasyPrint`) z wbudowaną mapą NMT PNG. | 3 wzorcowe raporty PDF w `reports/`. |
| **4. Frontend i Pitch** | `React + TS + MapLibre`: wejście 3 kliknięcia, Karta Decyzji, oś 10 lat. | 3 widoki mapy NMT + interaktywny Symulator „Co jeśli” + 9 sekcji README. | Testy Windows (Chrome/Edge/Firefox), film `≤ 3 min`, prezentacja `≤ 10 slajdów`. |

- **Konsultacja z mentorami KOWR:** Potwierdzenie tabeli parametrów `(Kc, Ky, p)` i przyrostu $\Delta\text{TAW}$ z **dr. inż. Tymoteuszem Bolewskim** oraz **dr. hab. Dariuszem Gozdowskim**.
- **Checkpoint Dnia 1 (22:00):** Wpisanie dowolnego `TERYT`, uprawy i daty z 10 lat zwraca pełny JSON 5 kroków KOWR i pobiera raport PDF w `< 20 s`.
- **Cut List awaryjny:** (1) Płaski raster 2D TWI + wektory GeoJSON zamiast 3D WebGL; (2) Analiza perfekcyjnej pojedynczej działki zamiast portfela wielu działek; (3) Jeśli `GUGiK NMT WCS` przekroczy 5 s, automatyczny fallback na DEM 30 m.
- **Kolejność dostaw (sekcja 8.1):** Najpierw działający potok live `mapa → ULDK → KBW SMSR → rekomendacja` na publicznym adresie. Potem `HydroFusion-PL` i tabele walidacji. Jury widzi tylko serwis, więc moduł bez śladu w interfejsie nie daje punktów.

---

## 7. Prezentacja (10 Slajdów), Demo 3 Min i Mapowanie na Kryteria Jury

1. **Slajd 1:** Tytuł i Hook (`≤ 18 s` od numeru działki do policzonego planu retencji).
2. **Slajd 2:** Problem polskiego rolnika i paradoks zachmurzenia Sentinel-2 (~55% dni).
3. **Slajd 3:** Przepływ 5 kroków KOWR i 5 kategorii darmowych danych otwartych.
4. **Slajd 4:** Silnik fizyczny FAO-56/33 + IUNG TAW + `WaterScore` i reguła absolutnego weta `WaterRisk`.
5. **Slajd 5:** `HydroFusion-PL`: rekonstrukcja pod chmurami, `TreeSHAP` (4 przyczyny), `Conformal 90%`, `AoA`.
6. **Slajd 6:** Mikrotopografia `NMT 1 m` na żywo: spadki, spływ D8, `TWI`, mikrozagłębienia.
7. **Slajd 7:** Symulator „Co jeśli” (retencja na najsuchszym roku dekady) + `FAO-RL-Irrigator` (tabela 10 lat).
8. **Slajd 8:** Walidacja przestrzenno-czasowa (holdout 2 woj. + lata 2018/2023 + 2024–2025) i jawność braków danych.
9. **Slajd 9:** Skalowalność krajowa (1,4 mln ha za `< 500 zł/mies.`), higiena RODO, czyste IP i mapa rozwoju (Etap 2: IoT + RL dla zastawek).
10. **Slajd 10:** Zaproszenie do testu na żywo dla 1–3 losowych działek wskazanych przez Jury.

| Kryterium KOWR | Waga | Główny argument punktowy |
| :--- | :---: | :--- |
| **Wartość praktyczna dla użytkownika** | **25%** | Ranking NWRM/agrotechniki osadzony na NMT 1 m działki, efekt policzony w dniach rezerwy i `%` plonu (FAO-33) + 2-stronicowy PDF. |
| **Dane i wiarygodność naukowa** | **25%** | 5 źródeł, zgodność z FAO-56/33 i IUNG-PIB KBW, separacja ML od fizyki, `WaterRisk Veto`, `AoA`, walidacja holdout i brak pozornej precyzji. |
| **Innowacyjność i potencjał rozwojowy** | **20%** | Kaskada `S2 10 m → NMT 1 m` + 2 lekkie modele ML (`HydroFusion-PL` + `PPO`) uruchamiane na CPU `< 80 ms` + skalowanie na zasób WRSP. |
| **Prostota obsługi** | **15%** | 3 kliknięcia (`Działka → Uprawa → Data`), zero żargonu GIS, 3 czytelne widoki mapy, działanie w przeglądarkach Windows i na telefonie bez instalacji. |
| **Działający prototyp i jakość techniczna** | **15%** | Równoległy potok `asyncio` (`10–18 s`, limit `≤ 30 s`), fallbacki na awarie API, pełna dokumentacja 9 sekcji README i czyste IP. |

---

## 8. Ustalenia z konsultacji z mentorami KOWR (21.11.2026)

> **Źródło:** Sesja pytań z członkiem jury KOWR, 21.11.2026. Mentor zaznaczył, że mówi we własnym imieniu, a nie w imieniu całego jury.  
> **Zasada:** Koncepcja z sekcji 1–7 zostaje. Ustalenia zmieniają priorytety, procedurę KBW i zakres upraw.

### 8.1. Pięć ustaleń o największym wpływie

1. **Jury ocenia tylko działający serwis webowy.** Jeden projekt dostaje ok. 3 minuty oceny. Repozytorium „raczej nikt nie będzie analizował”.
   - Moduł bez śladu w interfejsie nie daje punktów. Najpierw dostarczamy potok live `działka → KBW SMSR → rekomendacja`.
   - `HydroFusion-PL` i tabele walidacji wchodzą do produktu, gdy ich wynik widać w Karcie Decyzji.
   - Pierwszy ekran to mapa Polski z polem wyszukiwania. Serwis nie ma logowania ani ekranu powitalnego.
   - Dane podstawowe działki pojawiają się w `≤ 2 s`: identyfikator, gmina, obręb, powierzchnia, granica. Kolejne kroki doładowują się po kolei.
2. **Wzorcem jest System Monitoringu Suszy Rolniczej (SMSR) IUNG-PIB na poziomie działki.**
   - Mentor oczekuje „czegoś podobnego, tylko na poziomie działki, działającego”, plus rekomendacji.
   - Rolnik używa SMSR, bo na jego podstawie może ubiegać się o pomoc po dużej suszy.
   - Silnik liczy KBW według procedury SMSR (sekcja 8.2). Karta Decyzji pokazuje werdykt „susza wg progów IUNG: tak / nie” dla uprawy, kategorii gleby i okna.
   - Hipoteza (niepotwierdzona): dane referencyjne jury mają format zbliżony do werdyktów SMSR.
3. **Zero makiet (mock).** Jury wpisuje znany identyfikator działki. Jeden błędny fakt podstawowy ujawnia makietę.
   - `ULDK` działa na żywo dla każdego identyfikatora. Cache przyspiesza odpowiedź, ale nie zastępuje ścieżki live.
   - Przy awarii źródła interfejs pokazuje „brak danych”. Interfejs nie wstawia wartości zastępczej.
   - `ULDK` nie zwraca powierzchni ewidencyjnej. Interfejs podpisuje powierzchnię jako „z granic działki”.
4. **Zakres obejmuje wszystkie grunty rolne:** grunty orne, łąki, pastwiska i sady. Działki nierolnicze są poza zakresem.
   - Lista upraw przechodzi na 14 grup SMSR (sekcja 8.2) plus `użytki zielone`. Zmiana `CropCode` w `schemas.py` wymaga decyzji zespołu, bo zmienia kontrakt API.
   - Działkę nierolniczą rozpoznaje warstwa IUNG WMS `Susza:katgl_landuse2024` (`sgeo.iung.pl/geoserver/Susza/wms`). Kody 11–14 oznaczają las, wodę, zabudowę i komunikację.
   - Dla działki nierolniczej serwis zwraca komunikat zamiast analizy. Moduł `ml/rl_irrigator` (gałąź `feat/RL`) już korzysta z tej warstwy.
   - Użytki zielone nie mają progu SMSR. Dla nich działa sam bilans FAO-56, a `Pewność` spada o jeden poziom.
   - Gleby organiczne (kod 5) wymagają osobnej obsługi, bo dużo łąk leży na glebach organicznych.
5. **Każda zmiana urządzeń wodnych wymaga zgody Wód Polskich.** Dotyczy to zastawki, piętrzenia w rowie i zbiornika. Główny problem rolnictwa w Polsce to odwadnianie: sieć melioracyjna odprowadza wodę.
   - Ranking retencji ma dwie grupy działań.
   - Grupa „bez urządzeń wodnych”: międzyplon, mulcz, uprawa uproszczona, materia organiczna, pas buforowy.
   - Grupa „wymaga zgody Wód Polskich”: piętrzenie w rowie, zastawka, zbiornik, sterowanie odpływem. Każde działanie z tej grupy ma ten opis i link do PGW Wody Polskie.
   - Symulator „Co jeśli” liczy obie grupy bez zmian.
   - Pitch: zdanie „melioracja odprowadza wodę, którą można zatrzymać” wzmacnia hasło *zatrzymaj wodę tam, gdzie spadła*.
   - Do weryfikacji w Prawie wodnym: kiedy wystarcza zgłoszenie wodnoprawne, a kiedy potrzebne jest pozwolenie. Dotyczy to także poboru wody do nawadniania.

### 8.2. Procedura SMSR IUNG-PIB (sprawdzona 21.11.2026)

Strony źródłowe: `susza.iung.pulawy.pl` — `/system/`, `/progi/`, `/kategorie/`, `/wykazy/`, `/raporty/`.

| Element | Wartość w SMSR |
| :--- | :--- |
| **Wskaźnik** | $\text{KBW} = P - ET_p$. SMSR liczy $ET_p$ metodą Penmana. |
| **Okna** | 60 dni (6 dekad). Nowe okno startuje co 10 dni w sezonie 21.III–30.IX. Sezon ma 14 okien: `21.III–20.V`, `1.IV–31.V`, `11.IV–10.VI`, …, `21.VII–20.IX`, `1.VIII–30.IX`. |
| **Uprawy (14)** | Zboża ozime, zboża jare, kukurydza na ziarno, kukurydza na kiszonkę, rzepak i rzepik, ziemniak, burak cukrowy, drzewa owocowe, krzewy owocowe, truskawki, warzywa gruntowe, rośliny strączkowe, chmiel, tytoń. SMSR nie ocenia użytków zielonych. |
| **Kategorie gleb** | I: WOD `< 127,5 mm`. II: `127,5–169,9 mm`. III: `170–202,5 mm`. IV: `> 202,5 mm`. Źródło: mapa glebowo-rolnicza. |
| **Progi** | Tabela `/progi/` według Rozporządzenia MRiRW z 11.04.2019. Tabela podaje krytyczne KBW dla trójki `(uprawa, kategoria, okno)`. Progi są jednakowe dla wszystkich województw i odpowiadają spadkowi plonu o 20%. |
| **Przykład progu** | Zboża ozime, kat. I, okno `21.III–20.V`: `−129 mm`. Znak `x` oznacza brak strat w oknie. Znak `-` oznacza, że uprawa nie występuje na danej kategorii gleby. |
| **Publikacja** | Wyniki per gmina, lata 2020–2026, format HTML i PNG. SMSR nie udostępnia API ani WMS wyników. |

**Wpływ na silnik (sekcja 3.1):**
- Tabelę progów przepisujemy raz do `hydro_core` jako JSON ze wskazaniem źródła.
- Open-Meteo zwraca $ET_0$ FAO-56 Penman-Monteith. SMSR liczy $ET_p$ klasycznym Penmanem. README opisuje tę różnicę jako źródło rozbieżności z SMSR.
- Strona `/system/` podaje przykład `−140 mm` dla okna `1.IV–31.V` (kat. I, zboża). Tabela podaje `−132 mm` (ozime) i `−127 mm` (jare). Stosujemy tabelę, bo tabela cytuje rozporządzenie.
- Walidacja (sekcja 9 README): werdykt dla działki porównujemy z wykazem gminnym SMSR z lat 2020–2025.

### 8.3. Pozostałe ustalenia

| Ustalenie mentora | Wpływ na rozwiązanie |
| :--- | :--- |
| Link otwiera mapę Polski z działkami EGiB. Użytkownik wybiera działkę kliknięciem albo wpisuje identyfikator. Źródło działek to nasz wybór; starszy plik Parquet jest akceptowalny. | Koncepcja bez zmian (sekcja 4). Przy dużym zbliżeniu mapa pokazuje granice działek. Kliknięcie wywołuje `ULDK GetParcelByXY`. |
| Szybkość jest krytyczna, bo jury ocenia kilkadziesiąt projektów. | NMT 1 m nie blokuje wyniku. W sondzie 21.11.2026 `WCS GetCoverage` przekroczył 20 s. Widok „Gdzie ucieka woda” doładowuje się po Kroku 1 albo przechodzi na DEM 30 m. |
| Tylko funkcje bezpłatne. | Produkt i pitch nie mają warstw płatnych. Zgodne z sekcją 2. |
| Aplikacja webowa bez instalacji, także na telefonie. | Układ 60/40 ma wariant mobilny: mapa u góry, Karta Decyzji w arkuszu od dołu. |
| Okno 10 lat: mentor proponuje widok tego samego terminu we wszystkich latach, np. każdy czerwiec albo każdy 10 czerwca. | Oś 10 lat dostaje tryb „ten sam termin co roku” dla lat 2016–2025 z pozycją bieżącego roku. Archiwum Open-Meteo zwraca 10 lat w jednym zapytaniu, więc tryb nie wydłuża analizy. |
| Stan na dziś i porada na najbliższy sezon. W sezonie: „nawodnij dawką X” albo „poczekaj, za 5 dni prognozowany opad”. | Potwierdza Krok 4: prognoza Open-Meteo w regule FAO-56 i w PPO. Poza sezonem (1.X–20.III) skrót *Dziś* pokazuje podsumowanie ostatniego sezonu i działania przed wiosną. Ten widok nie daje decyzji o nawadnianiu. Ocena jury odbywa się w listopadzie, czyli poza sezonem. |
| Wskaźniki i skalę wybiera zespół. Skala 1–10 jest akceptowalna. | `WaterScore (0–100)` zostaje. |
| Wilgotność gleby w % jest opcjonalna i ma duży błąd. W jednej działce różnica sięga kilkudziesięciu % przez spływ (np. 50% i 10%). | Serwis nie podaje jednej wartości % dla działki. Zmienność w działce pokazuje warstwa TWI i spływu, co wspiera NMT 1 m. |
| Mentor nie sprawdza licencji. Oficjalnie zalecane są dane otwarte. Mentor wspomniał Google Earth Engine i embeddingi satelitarne Google. | Zostajemy przy danych otwartych (§ 5 Regulaminu). Nie dodajemy GEE: wymaga logowania, ma limity i wydłuża czas odpowiedzi. |

### 8.4. Pytania otwarte

1. Czy pozostali członkowie jury też oceniają tylko serwis? Mentor mówił we własnym imieniu.
2. Jaki format mają prywatne dane referencyjne jury?
3. Czy jury testuje daty spoza sezonu 21.III–30.IX?
4. Jakie kody warstwy `katgl_landuse2024` mają łąki i pastwiska?
