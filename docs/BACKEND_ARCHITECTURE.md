# AgroRetencja KOWR — Architektura backendu

> Dokument dla zespołu: jak działa backend, gdzie dodać nową zmienną, regułę albo źródło danych i dlaczego tak.
> Stan: 21.11.2026. Uzupełnia [SOLUTION_BLUEPRINT.md](SOLUTION_BLUEPRINT.md) i [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md).

---

## 1. Warstwy

```text
backend/main.py            FastAPI: endpointy, CORS, mapowanie błędów na HTTP
        │
backend/analysis.py        AnalysisService: kroki 1–5 KOWR, cache wyników (L1), „Co jeśli”
backend/irrigation.py      łańcuch dostawców porad nawadniania (RL → reguła FAO)
backend/map_layers.py      warstwy GeoJSON mapy
backend/pdf/               raport 2-stronicowy (łańcuch rendererów)
backend/inference/         HydroFusion-PL (ładuje wagi, jeśli istnieją)
        │
backend/clients/           I/O: klienci źródeł danych, rejestr źródeł, pipeline zbierania danych
        │
hydro_core/                czysta logika bez I/O: oś czasu, bilans, teren, zmienne, ocena, przyczyny,
                           retencja, historia, porady; konfiguracja w hydro_core/config/*.yaml
        │
kowr_rl (ml/rl_irrigator)  fizyka FAO-56 (balance_step, relative_yield) + polityki RL i bazowe
```

**Zasada:** `hydro_core` nie pobiera danych i nie zna FastAPI, więc testuje się go bez sieci. Każde pobranie danych jest w `backend/clients`.

---

## 2. Wzorzec Rejestr: punkty rozszerzeń

Wszystkie „listy rzeczy” w silniku to obiekty `hydro_core.registry.Registry`. Kolejność rejestracji to kolejność wykonania i wyświetlania. Endpoint `GET /api/registries` pokazuje, co jest zarejestrowane.

| Rejestr | Plik | Co dodajesz |
| :--- | :--- | :--- |
| `FEATURES` | `hydro_core/features.py` | zmienną (np. `vpd_15`) z grupą KOWR, jednostką i opisem |
| `SCORE_COMPONENTS` | `hydro_core/scoring.py` | składnik WaterScore 0–100; waga i filar w `config/scoring.yaml` |
| `VETO_RULES` | `hydro_core/scoring.py` | regułę weta: zwraca `VetoHit(poziom, powód)` |
| `CONFIDENCE_CHECKS` | `hydro_core/scoring.py` | wymiar „Pewności danych” (ostrzeżenie + kara) |
| `COUNTERFACTUALS` | `hydro_core/causes.py` | scenariusz kontrfaktyczny przyczyny (krok 2) |
| `APPLICABILITY` | `hydro_core/retention.py` | warunek stosowalności zabiegu (nazwa użyta w YAML) |
| zabiegi retencyjne | `config/retention_measures.yaml` | nowy zabieg: ΔTAW, ΔCN, grupa, czas do efektu |
| `ADVICE_RULES` | `hydro_core/advisor.py` | regułę porady dla rolnika |
| `SOURCES` | `backend/clients/registry.py` | źródło danych: klasa klienta + budowa argumentów |
| `SOIL_PROVIDERS` | `backend/clients/registry.py` | kolejne źródło kategorii gleby (łańcuch) |
| `IRRIGATION_PROVIDERS` | `backend/irrigation.py` | dostawcę porad nawadniania (łańcuch) |
| `MAP_LAYERS` | `backend/map_layers.py` | warstwę mapy GeoJSON |
| `RENDERERS` | `backend/pdf/report.py` | sposób renderowania raportu (łańcuch) |

### Przykład: nowa zmienna we wzorze WaterScore

1. Zarejestruj zmienną w `hydro_core/features.py`:

   ```python
   @feature("p_7", group="klimat", unit="mm", label="Opad 7 dni")
   def _p7(fx: FeatureContext) -> float | None:
       return fx.timeline.sum_before(fx.timeline.precip, 7)
   ```

2. Użyj jej w składniku w `hydro_core/scoring.py` (nowym albo istniejącym):

   ```python
   @component("opad_tygodnia", "Opad ostatniego tygodnia")
   def _c_p7(fx: FeatureContext) -> ComponentResult:
       p7 = fx.get("p_7")
       if p7 is None:
           return None, None  # brak danych -> waga przechodzi na pozostałe składniki
       return min(100.0, 100.0 * p7 / 25.0), f"Opad 7 dni: {p7:.0f} mm."
   ```

3. Nadaj wagę w `hydro_core/config/scoring.yaml`:

   ```yaml
   components:
     opad_tygodnia: {weight: 0.05, pillar: balance}
   ```

Zmienna jest od razu dostępna także dla:
- modelu ML (`FeatureContext.vector(["p_7", ...])`),
- rozbicia przyczyn (grupa `klimat`),
- porad i reguł weta (`fx.get("p_7")`).

Składnik z wartością `None` nie psuje wyniku: jego waga przechodzi proporcjonalnie na pozostałe (blueprint 3.2).

---

## 3. Decyzje architektoniczne (ADR)

### ADR-1: Jedna oś czasu dla historii i prognozy (zasada „as-of”)

**Decyzja:** `hydro_core.timeline.DailyTimeline` to jedna seria dzienna (opad, ET₀, Tmax). Każdy dzień ma etykietę:

| `kind` | Znaczenie |
| :--- | :--- |
| `observed` | archiwum ERA5 (Open-Meteo) lub zapas NASA POWER |
| `recent` | ostatnie dni, których archiwum jeszcze nie ma (analiza modelu prognozy) |
| `forecast` | prognoza ECMWF IFS po dacie analizy (tylko gdy data analizy ≈ dziś) |
| `climatology` | średnia z innych lat dla tego dnia roku (±7 dni): dni po dacie historycznej i luki w danych |

**Zasada as-of:** oś zawiera to, co było wiadomo rano w dniu analizy. Dla daty z przeszłości dni po niej to klimatologia, nigdy prawdziwa pogoda „z przyszłości”. Tak samo działa moduł RL.

**Skutek:**
- Jeden ciągły bilans FAO-56 (`fao_balance.run_balance`) liczy na tej samej osi 10 lat historii, stan dziś i projekcję.
- Z projekcji wynikają „dni do stresu”, `water_state_timeline` (flaga `forecast`) i porady typu „przygotuj nawadnianie”.

**Dlaczego:** wcześniej historia (KBW, 10 lat) i prognoza (krok 4) byłyby liczone osobno, na różnych danych, i mogłyby sobie przeczyć.

### ADR-2: Fizyka bilansu tylko w jednym miejscu

**Decyzja:** dobowy krok bilansu to `kowr_rl.simulator.balance_step`, czyli funkcja, na której trenowano agenta RL.
- `hydro_core` dodaje do niej spływ SCS-CN (opad efektywny P − RO) i oś scenariuszy B: wariant bazowy, każdy zabieg, kombinacje — w jednym przebiegu.
- Parametry upraw: źródłem jest `ml/rl_irrigator/config/crops.yaml` (15 upraw i aliasy kodów API, np. `pszenica_jara` → `zboza_jare`); `hydro_core/config/crops_extra.yaml` (ten sam format) uzupełnia tylko kody, których RL nie zna.
- TAW kategorii IUNG: `ml/rl_irrigator/config/soils.yaml`.

**Dlaczego:** kroki 1 i 4 nie mogą liczyć bilansu różnymi wzorami (*training-serving skew*).

### ADR-3: Integracja modułu RL przez `_prefetched`, bez kopiowania kodu

**Decyzja:**
- `kowr_rl.inference.recommend()` dostał jeden opcjonalny parametr `_prefetched=(kod_IUNG, pogoda, źródła, prognoza)`. To ten sam wzorzec, który już miało `compare_strategies()`.
- Backend przekazuje swoją oś czasu, więc RL nie pobiera drugi raz IUNG i archiwum Open-Meteo (limit 10 000 zapytań/dobę).
- Bez parametru moduł działa jak dawniej; `/rl/*` jest podpięty w `main.py`.

### ADR-4: Łańcuchy odpowiedzialności zamiast `if/else`

Te elementy działają jako łańcuch — pierwszy działający element wygrywa:
- **Nawadnianie:** `rl` (uprawy, na których trenowano agenta) → `fao` (reguła FAO-56 dla każdej uprawy).
- **Gleba:** IUNG → SoilGrids.
- **Raport:** WeasyPrint → Matplotlib PDF (bez GTK, działa na każdym Windows) → HTML.

Każde przejście na kolejny element jest zapisane w `audit.model_versions`.

### ADR-5: Brak danych zamiast wartości zastępczych (ustalenie mentora 8.1.3)

| Sytuacja | Odpowiedź |
| :--- | :--- |
| Działka nierolnicza wg IUNG (las, woda, zabudowa) | HTTP 422 `{"status": "not_arable"}` z komunikatem, bez analizy |
| Brak pogody lub gleby | HTTP 503 `{"status": "no_data"}` |
| ULDK przeciążone (przekierowanie GUGiK na `/resp/uldk.php`, zaobserwowane 21.11.2026) | ponowienie; jeśli nie pomoże, HTTP 503 `source_unavailable` |
| Inne brakujące źródło | analiza idzie dalej; brak jest widoczny w `confidence.missing_factors` i w audycie |

### ADR-6: Przyczyny niedoboru zawsze fizyczne; HydroFusion-PL tylko pod chmurami

Krok 2 liczy dla ostatnich 60 dni, o ile mniejszy byłby niedobór transpiracji Σ(ETc − ETa) w czterech scenariuszach:
- **klimat:** pogoda przeciętna dla działki,
- **gleba:** gleba kat. IV,
- **spływ:** brak spływu,
- **uprawa:** Kc = 1.

Wyniki normalizuje się do 100%.

**HydroFusion-PL** (wytrenowany 22.11.2026, [ml/hydrofusion/README.md](../ml/hydrofusion/README.md)) nie służy do przyczyn, bo uczono go bez informacji o uprawie, więc jego TreeSHAP nie oceni czynnika „uprawa”. Model odtwarza tylko NDMI, gdy w ostatnich 15 dniach nie ma bezchmurnego zdjęcia.

**Wejście modelu:** ostatnie czyste zdjęcie z okna 16–60 dni. Klient Sentinel-2 dostarcza je jako `S2ParcelSeries.previous`, a okno ustawia `AGRO_S2_PREVIOUS_WINDOW_DAYS`.

**Poza obszarem stosowalności** (DI > próg) model się nie włącza, a pewność danych spada o jeden poziom. Funkcja `causes.attribution_from_shap` zostaje na przyszły model, który pozna uprawę.

### ADR-7: `water_state` −5…+5 i oś dzienna

- `water_state = (WaterScore − 50) / 10`, ograniczone przez ryzyko: KRYTYCZNE ≤ −3, WYSOKIE ≤ −1 (`scoring.yaml`). Weto nigdy nie jest uśredniane.
- Na osi dziennej zmienia się składnik „zapas wody w glebie”. Pozostałe składniki są zamrożone na dzień analizy, więc wartość w dniu analizy równa się wynikowi głównemu.

### ADR-8: Cache warstwowy

- **L1:** `AnalysisCache` w procesie (LRU + TTL 1 h) po kluczu `(działka, uprawa, data)`. PDF i „Co jeśli” nie liczą analizy od nowa.
- **Cache źródeł:** SQLite (`data/cache/sources.sqlite`), patrz klienci.
- **L2 w produkcji:** Redis/PostGIS z tym samym kluczem (README, plan skalowania).

---

## 4. Przepływ jednej analizy (`POST /api/analyze`)

1. `ParcelDataCollector.collect`: ULDK, potem wszystkie `SOURCES` równolegle z limitem 25 s.
2. `build_timeline`: historia + prognoza albo klimatologia.
3. `analyse_terrain`: spadek, zagłębienia (priority-flood), spływ D8, TWI. Siatka jest uśredniana do ≤ 300 × 300 komórek.
4. `run_balance`: ciągły bilans FAO-56 + SCS-CN od pierwszego dnia osi.
5. `FeatureContext`: leniwe, zapamiętywane zmienne. Jeśli są wagi HydroFusion-PL, uzupełniają NDMI pod chmurami.
6. Krok 1: `compute_water_score` → `assess_risk` (weta) → `assess_confidence` → `water_state` + oś dzienna → `build_history` (10 lat, ten sam dzień co roku).
7. Krok 2: `attribute_causes` (albo TreeSHAP).
8. Krok 3: `build_retention_plan`: każdy stosowalny zabieg symulowany na najsuchszym sezonie dekady; ranking w grupach „bez urządzeń” / „wymaga zgody”.
9. Krok 4: `advise_irrigation` (łańcuch RL → FAO).
10. `build_advice`: porady oparte na projekcji, KBW SMSR i wynikach kroków 3–4.
11. `build_map_layers` + audyt (źródła, wersje modeli, czas).

Czas na prawdziwej działce (21.11.2026): 7–21 s, z czego 6–9 s to Sentinel-2 i Open-Meteo. Obliczenia trwają poniżej 1 s.

---

## 5. Endpointy

| Metoda | Ścieżka | Opis |
| :--- | :--- | :--- |
| GET | `/api/health` | status, uprawy, źródła, wersja HydroFusion |
| GET | `/api/registries` | wszystkie punkty rozszerzeń i ich zawartość |
| GET | `/api/parcels/search?q=` | wyszukiwanie obręb + numer (ULDK) |
| GET | `/api/parcel?parcel_id=` lub `?lat=&lon=` | szybkie dane działki (≤ 2 s) |
| POST | `/api/analyze` | `AnalysisResponse` (kroki 1–5 + `advice`) |
| POST | `/api/what-if` | `{parcel_id, crop, analysis_date, enabled_measures}` → `WhatIfScenarioResult` |
| GET/POST | `/api/report.pdf` | raport; nagłówek `X-Report-Renderer` mówi, którym rendererem powstał |
| * | `/rl/recommend`, `/rl/compare`, `/rl/health` | moduł nawadniania (gałąź `feat/RL`) |

**Nowe pola odpowiedzi (wszystkie z wartościami domyślnymi, kontrakt jest wstecznie zgodny):**
- `assessment`: `water_state`, `water_state_timeline`, `storage_pct_taw`, `days_to_stress`, `kbw_is_drought`, `in_season`, `crop_stage`, `kc_today`, `runoff_60d_mm`, `water_score.components`.
- `retention.recommendations[*]`: `group`, `requires_permit`, `permit_note`, `permit_url`, `effect_reserve_days`, `effect_yield_pp`, `retained_runoff_mm`, `time_to_effect`.
- `irrigation`: `policy_used`, `plan_7d`, `in_season`, `limitations`.
- `advice[]`: porady z priorytetem 1–5, horyzontem i liczbami (`evidence`).
- `history_10y[*]`: `same_day_state`, `same_day_storage_pct_taw`, `is_partial`.

---

## 6. Uruchomienie i testy

```bash
uv sync --group dev
uv run uvicorn backend.main:app --host 0.0.0.0 --port 8000
uv run pytest tests                 # backend, bez sieci (atrapa kolektora danych)
uv run pytest ml/rl_irrigator/tests # moduł RL
uv run python scripts/probe_sources.py --parcel 302111_5.0015.72 --date 2025-07-15
```

Frontend: `VITE_API_URL=http://localhost:8000` w `frontend/.env`. Dozwolone originy CORS ustawia `AGRO_CORS_ORIGINS` w `.env`.

---

## 7. Do weryfikacji z mentorami

Wszystkie poniższe parametry są w plikach YAML, więc zmiana nie wymaga kodu:
- **Parametry 3 nowych upraw** (`crops_extra.yaml`): pszenica jara, rzepak ozimy, użytki zielone.
- **Efekty zabiegów** (`retention_measures.yaml`): ΔTAW i ΔCN to założenia literaturowe.
- **Bazowe CN kategorii IUNG** (`runoff.py`) i wagi WaterScore (`scoring.yaml`).
