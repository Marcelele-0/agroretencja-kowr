# Rusałka (AgroRetencja KOWR): Plan Implementacji i Architektury

> **Repozytorium:** Prywatny monorepo (`agroretencja/`), kod pisany w 100% od zera pod KOWR (§ 5 Regulaminu HackNation 2026).  
> **Hardware treningowy (PC):** i7-14700K (28 wątków CPU) + RTX 4070 Ti SUPER (16 GB VRAM).  
> **Cel czasowy:** Pełny potok działki `≤ 30 s` (cel: `10–18 s`), stabilny na przeglądarkach Windows.

---

## 1. Decyzja Architektoniczna: 1 Prywatne Monorepo

Wybieramy **jedno prywatne monorepo** zamiast dwóch oddzielnych repozytoriów (`training` i `app`):

1. **Jeden spójny kontrakt (`src/hydro_core/`):** Definicje cech, progi IUNG TAW, bilans FAO-56/33 i schematy Pydantic (`schemas.py`) są współdzielone przez trening ML i produkcyjny backend. Eliminuje to błąd rozjazdu cech (*training-serving skew*) w 24-godzinnym sprincie.
2. **Jedno prywatne repozytorium do oddania jury (§ 4.3 Regulaminu):** KOWR ocenia jedno źródło wraz z metodologią weryfikacji i pełnym wykazem kodu oraz wag modeli.
3. **Lekkie środowisko produkcyjne dzięki izolacji w `uv`:** Zależności ciężkie (`torch`, `stable-baselines3`, `gymnasium`, `optuna`, `pandas`) siedzą wyłącznie w grupie `[dependency-groups].train`. Aplikacja serwerowa nie ładuje PyTorcha ani SB3:
   - `HydroFusion-PL` zapisuje natywny model LightGBM (`artifacts/models/hydrofusion_*.txt`) i liczy **TreeSHAP w C++** przez `pred_contrib=True`.
   - `FAO-RL-Irrigator` eksportuje wagi aktora PPO do lekkiego pliku **NumPy `.npz`** (`artifacts/models/ppo_irrigator_weights.npz`).
4. **Czysta separacja IP (§ 5 Regulaminu):** Materiały zewnętrzne i notatki wstępne w `../docs/` leżą **poza** repozytorium Git projektu `agroretencja/`.

---

## 2. Mapa Katalogów i Podział Własności (Praca Równoległa)

```text
agroretencja/
├── pyproject.toml              # Zależności CPU runtime + grupa [train] i [dev]
├── .python-version             # 3.11 (zgodność wheel dla rasterio/lightgbm/torch)
├── README.md                   # 9 obowiązkowych sekcji KOWR (pkt 4.2 + 5.3 + 5.5)
├── docs/
│   ├── SOLUTION_BLUEPRINT.md   # Pełna specyfikacja produktu, fizyki i modeli ML
│   └── IMPLEMENTATION_PLAN.md  # Ten dokument wdrożeniowy
├── src/
│   ├── hydro_core/             # [TOR A] Kontrakty Pydantic, fizyka FAO-56/33, NMT TWI/D8, ocena i veto
│   │   └── schemas.py
│   └── backend/
│       ├── clients/            # [TOR A] 6 asynchronicznych klientów API + cache SQLite
│       ├── inference/          # [TOR C] Ładowanie lekkich wag LightGBM i NumPy PPO + AoA
│       ├── pdf/                # [TOR C] Generator 2-stronicowego audytowalnego PDF
│       └── main.py             # [TOR C] FastAPI (/api/analyze, /api/what-if, /api/report.pdf)
├── ml/
│   ├── hydrofusion/            # [TOR B] Pobranie S2 COG + Open-Meteo + SoilGrids + trening GBDT
│   └── rl_irrigator/           # [TOR B] Środowisko Gymnasium FAO-56/33 + trening PPO + eksport .npz
├── frontend/                   # [TOR D] Vite + React + TS + MapLibre GL JS (CSS z design systemu Rusałka, bez Tailwind)
├── artifacts/
│   ├── models/                 # Zamrożone lekkie wagi produkcyjne (< 20 MB łącznie)
│   └── validation/             # Raporty JSON + wykresy holdout do Sekcji #9 README
├── data/                       # Lokalne bufory (ignorowane przez git poza metadanymi)
├── reports/                    # Wzorcowe raporty PDF dla jury
├── scripts/                    # Skrypty benchmarku ≤ 30 s i sanity-checków API
└── tests/                      # Testy jednostkowe i integracyjne pytest
```

**Własność modułów (zero konfliktów przy równoległej pracy):**
- **Tor A (Rdzeń i Dane):** `src/hydro_core/`, `src/backend/clients/`, `tests/test_hydro_core.py`, `tests/test_clients.py`.
- **Tor B (Treningi ML):** `ml/rl_irrigator/`, `ml/hydrofusion/`, `artifacts/models/`, `artifacts/validation/`.
- **Tor C (API i PDF):** `src/backend/inference/`, `src/backend/pdf/`, `src/backend/main.py`, `tests/test_api.py`.
- **Tor D (Frontend i Zgłoszenie):** `frontend/`, `README.md`, `reports/`, prezentacja 10 slajdów, film 3 min.

---

## 3. Zweryfikowane Źródła API (Sonda Live 21.11.2026)

| Kategoria | Usługa / Endpoint | Status sondy | Latencja | Uwagi wdrożeniowe i fallback |
| :--- | :--- | :---: | :---: | :--- |
| **Granice działek** | `uldk.gugik.gov.pl` (`GetParcelById` / `GetParcelByXY`) | `HTTP 200` | `0,30 s` | Zwraca `TERYT + SRID=2180 POLYGON`. Fallback: `0_dzialki.parquet`. |
| **NMT 1 m** | `mapy.geoportal.gov.pl/wss/service/PZGIK/NMT/GRID1/WCS/DigitalTerrainModel` | `HTTP 200` | `0,91 s` (GetCapabilities) | `WCS GetCoverage` na bbox działki przekraczał 20 s: backend musi buforować wycinki. Fallback: DEM 30 m. |
| **Ortofotomapa** | `mapy.geoportal.gov.pl/wss/service/PZGIK/ORTO/WMTS/StandardResolution` (WMTS, `EPSG:3857`) | `HTTP 200` | `0,09 s` / kafel | Cache 30 dni. WMS GetMap: 0,4–0,6 s / kafel bez cache; frontend używa WMS tylko od zoomu 16. |
| **Granice działek (mapa)** | `integracja.gugik.gov.pl/cgi-bin/KrajowaIntegracjaEwidencjiGruntow` (KIEG WMS) | `HTTP 200` | `0,45 s` / kafel | Powyżej ok. 1,8 m/px zwraca pusty obraz: frontend prosi o 1024 px na kafel 512 px od zoomu 15. |
| **Gleba i użytkowanie** | `sgeo.iung.pl/geoserver/Susza/wms` (`Susza:katgl_landuse2024`, GetFeatureInfo) | `HTTP 200` | `0,31 s` | Kategoria gleby I–IV oraz kody lasu, wody i zabudowy. |
| **Meteo 10 lat** | `archive-api.open-meteo.com/v1/archive` + `api.open-meteo.com/v1/forecast` | `HTTP 200` | `0,12–0,46 s` | Limit ok. 600 zapytań/min na adres; długi zakres liczy się jako wiele zapytań (ok. 1 na 14 dni). Frontend pobiera tylko potrzebne zakresy, przez pośrednika serwera i z cache przeglądarki. |
| **Meteo PL live** | `danepubliczne.imgw.pl/api/data/synop` | `HTTP 200` | `0,15 s` | Dane bieżące ze stacji synoptycznych IMGW-PIB. |
| **Satelita S2** | `earth-search.aws.element84.com/v1/collections/sentinel-2-l2a` | `HTTP 200` | `0,55 s` | STAC search + `rasterio` COG HTTP Range na `B04, B08, B8A, B11, SCL`. |
| **Gleba** | `rest.isric.org/soilgrids/v2.0/properties/query` + `susza.iung.pulawy.pl` | `HTTP 200` | `1,12 s` / `0,13 s` | `sand, clay, soc` (0–60 cm) mapowane do 4 kategorii `TAW` IUNG-PIB; cache SQLite. |
| **Tło suszowe** | `drought.emergency.copernicus.eu/api/wms` | `HTTP 200` | `0,26 s` | Uwaga: w PDF podano URL `/data/wms-service` (zwraca HTML), działający endpoint WMS to `/api/wms`. |
| **Hydrografia** | `mapy.geoportal.gov.pl/wss/service/PZGIK/BDOT/WMS/PobieranieBDOT10k` | `HTTP 200` | `0,31 s` | Uwaga: `wody.isok.gov.pl/wss/INSPIRE` i `WFS/SiecWodna` zwracają `HTTP 401`, a publiczne `overpass-api.de` ma `502`/timeout. Używamy publicznego BDOT10k WMS / pre-fetched warstwy cieków. |

---

## 4. Pięć Faz Implementacji z Definicją Ukończenia (DoD)

### Faza 1 (H0–H3) — Rdzeń Fizyki `hydro_core`
- **Zakres:**
  - `crops.py`: tabele `Kc(faza)`, `Ky`, `p` (FAO-56 / FAO-33) dla 7 głównych upraw w Polsce.
  - `fao_balance.py`: wektoryzowany symulator dobowy FAO-56 + FAO-33 + IUNG `KBW_60` + progi `TAW` (`<127,5`, `127,5–170`, `170–202,5`, `>202,5 mm`).
  - `topography.py`: obliczanie spadku $\beta$, kierunku spływu `D8`, `TWI` oraz mikrozagłębień z wycinka NMT `numpy`.
  - `scoring.py`: dwuosiowa ocena `WaterScore (0–100)` + `WaterRisk (Veto)` + osobna `DataConfidence (AoA)` + ranking zabiegów retencyjnych i kalkulator „Co jeśli”.
- **DoD i weryfikacja:** `uv run pytest tests/test_hydro_core.py` — 100% testów zielonych, deterministyczny bilans 10 lat wykonuje się w `< 50 ms`.

### Faza 2 (H2–H6, równolegle) — Klienci API + FastAPI + Benchmark ≤ 30 s
- **Zakres:**
  - 6 asynchronicznych klientów w `src/backend/clients/` z `httpx.AsyncClient`, twardym limitem czasu `8.0 s`, cache SQLite i jawnym oznaczaniem trybu (`live` / `pre_fetched` / `fallback`).
  - Endpointy FastAPI: `POST /api/analyze`, `POST /api/what-if`, `POST /api/report.pdf`, `GET /api/health`.
- **DoD i weryfikacja:** `uv run python scripts/benchmark_20_parcels.py` — 20 losowych działek z różnych województw i losowych dat (2016–2026) zwraca pełne `AnalysisResponse` z medianą `≤ 15 s` i `P95 ≤ 25 s`.

### Faza 3 (H2–H7, równolegle na i7-14700K + RTX 4070 Ti SUPER) — Dwa Lekkie Treningi ML
- **Tor ML-1 (`ml/rl_irrigator/`):**
  1. Pobranie 60 punktów Polski × 10 lat (`Open-Meteo` — ~30 s).
  2. Środowisko `Gymnasium` oparte bezpośrednio o `fao_balance.py` (train: 2016–2023, test: 2024–2025; dla dat historycznych prognoza ma skalibrowany szum).
  3. Trening PPO (`stable-baselines3`, 16 env, `1M` kroków ≈ 3–5 min) + porównanie z regułą progową FAO-56 + eksport wag do `artifacts/models/ppo_irrigator_weights.npz` i metryk do `artifacts/validation/rl_eval.json`.
  - **Reguła włączenia:** Jeśli PPO nie oszczędza wody lub nie poprawia WUE względem reguły FAO na latach 2024–2025, Krok 4 używa jawnie reguły FAO-56.
- **Tor ML-2 (`ml/hydrofusion/`):**
  1. Pobranie ~35–65 tys. czystych obserwacji `3×3 px` dla 300–600 punktów PL × 10 lat przez `Earth Search STAC + COG Range` + `Open-Meteo` + `SoilGrids` + `GUGiK NMT` (~30–45 min).
  2. Trening 3 modeli kwantylowych LightGBM ($\tau \in \{0.05, 0.50, 0.95\}$) na GPU/CPU (~10–20 min) + Split Conformal Prediction + statystyki AoA ($DI$).
  3. Walidacja holdout (2 wyłączone województwa + lata suszy 2018 i 2023) → zapis modeli do `artifacts/models/` oraz metryk (`RMSE`, `MAE`, `R²`, `PICP 90%`, `IUNG F1`) do `artifacts/validation/hydrofusion_eval.json`.
- **DoD i weryfikacja:** Modele załadowane w CPU runtime (`src/backend/inference/`) zwracają wynik w `< 50 ms`; wszystkie testy walidacji spełniają progi lub uruchamiają jawny fallback fizyczny.

### Faza 4 (H5–H12, równolegle) — Frontend Web + Generator PDF
- **Zakres:**
  - `frontend/` utworzone przez `pnpm create vite`: React + TypeScript + Tailwind + MapLibre GL JS.
  - 3 kliknięcia na wejściu (`TERYT / klik na mapie` + `Uprawa` + `Data z 10 lat`), układ 60% Karta Decyzji / 40% Mapa z 3 widokami (*Gdzie ucieka woda* / *Wilgotność* / *Plan działań*).
  - Interaktywne przełączniki Symulatora „Co jeśli” wywołujące `/api/what-if`.
  - `src/backend/pdf/`: kompilacja 2-stronicowego audytowalnego PDF (Strona 1: decyzja, mapa NMT, rekomendacje; Strona 2: źródła, daty, niepewność, RODO).
- **DoD i weryfikacja:** `pnpm build` w `frontend/` przechodzi bez błędów; wygenerowany `reports/sample_report.pdf` otwiera się poprawnie i zawiera wszystkie 5 kroków KOWR.

### Faza 5 (H12–H24) — Integracja, Testy Windows, README i Pitch
- **Zakres:**
  - Test E2E na Windows (Chrome, Edge, Firefox) z losowymi działkami i datami.
  - Uzupełnienie Sekcji #9 w `README.md` realnymi metrykami z `artifacts/validation/`.
  - Wygenerowanie 3 wzorcowych raportów PDF do `reports/`.
  - Nagranie filmu demo (`≤ 3 min`) i eksport prezentacji PDF (`≤ 10 slajdów`).
- **DoD:** Pełna checklista zgłoszeniowa KOWR (pkt 4.1 i 4.2 zadania) zaliczona w 100%.

---

## 5. Ryzyka i Automatyczne Fallbacki

| Ryzyko | Zabezpieczenie i fallback |
| :--- | :--- |
| Timeout lub awaria `GUGiK NMT WCS 1 m` / `ULDK` na sali | Twardy timeout `6–8 s` → przełączenie na lokalny cache DEM 30 m / `0_dzialki.parquet` i jawna adnotacja w `DataConfidence` i na 2. stronie PDF. |
| Zachmurzenie `> 30%` na `Sentinel-2` w dacie wskazanej przez jury | `HydroFusion-PL` rekonstruuje $\widehat{\text{NDMI}} \pm \Delta_{90\%}$; gdy punkt jest poza domeną AoA ($DI > \tau_{95}$), system przechodzi na czysty bilans Meteo+Gleba+NMT i zmienia odznakę pewności na `OGRANICZONA`. |
| Błąd `HTTP 401` na serwerach WFS Wody Polskie / ISOK | Odpytujemy publiczny `Geoportal BDOT10k WMS` + buforowaną sieć hydrologiczną Polski oraz `EDO /api/wms`. |
| Brak zależności systemowych `WeasyPrint` (GTK/Pango) na innym hoście | Awaryjna ścieżka renderowania PDF lub kontener `Dockerfile` dla prostego uruchomienia przez jury. |

---

## 6. Stan prac (21.11.2026)

| Faza | Stan | Uwagi |
| :--- | :--- | :--- |
| 1. Rdzeń fizyki `hydro_core` | w toku (Asia) | Wzór stanu wody i źródła składowe. Wynik: `assessment.water_state`, `assessment.components`, `audit.sources`. |
| 2. Klienci API + FastAPI | w toku | Frontend ma własnych klientów ULDK, IUNG i Open-Meteo (`frontend/src/lib/`). |
| 3. Trening ML: `FAO-RL-Irrigator` | gotowy (Piotr, gałąź `feat/RL`) | `/rl/recommend`, `/rl/compare`; opis: `docs/RL_IRRIGATION_DECISION_MODEL.md`. |
| 3. Trening ML: `HydroFusion-PL` | nie rozpoczęty | Kolejność według blueprintu 8.1: najpierw działający potok, potem ML. |
| 4. Frontend web | gotowy (`main`) | Opis: `frontend/README.md`. Raport PDF: druk strony do czasu `/api/report.pdf`. |
| 5. Integracja, testy, README, pitch | w toku | README w 9 sekcjach zaktualizowane 21.11.2026. |

