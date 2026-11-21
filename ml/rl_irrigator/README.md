# Moduł nawadniania (krok 4 KOWR): bilans FAO-56 + agent RL

Moduł odpowiada na pytanie rolnika: **„czy podlać w najbliższych 7 dniach i ile mm?”**. To ostatni, opcjonalny krok analizy KOWR: ocena sytuacji wodnej → przyczyny → retencja → agrotechnika → **nawadnianie**.

Dla dowolnej działki rolnej w Polsce i dowolnej daty z ostatnich 10 lat moduł:
1. pobiera na żywo pogodę i kategorię gleby;
2. odtwarza dobowy bilans wody w strefie korzeniowej od początku sezonu;
3. daje plan dawek na 7 dni z uzasadnieniem liczbami, źródłami, niepewnością i ograniczeniami.

Rekomendacja jest liczona z pobranych danych, a nie wybierana z gotowych odpowiedzi.

> **Status agenta RL: domyślna strategia modułu (`default_policy: rl`).** Na czystym teście, czyli 50 nowych lokalizacji niewidzianych w treningu ani przy wyborze modelu, lata 2025–2026 i prawdziwe prognozy ECMWF, agent **przy każdym budżecie wody (50/100/150/250 mm) daje istotnie wyższy plon i zużywa istotnie mniej wody niż reguła FAO-56**. Łącznie to +0,23 pkt proc. plonu i −6,0 mm (−7%) wody na sezon (81 wobec 87 mm). Bije też najlepszą prostą regułę („małe dawki + prognoza”). Szczegóły: [`reports/evaluation_holdout.md`](reports/evaluation_holdout.md) i [`EXPERIMENTS.md`](EXPERIMENTS.md) (E6). Reguły FAO są dostępne przez `policy="fao_rule"`, `"fao_forecast"` i `"fao_small"`.

---

## 1. Jak uruchomić (uv, Windows / Linux)

Wszystkie polecenia uruchamia się z katalogu głównego repozytorium. Moduł korzysta z głównego projektu uv.

```bash
# runtime (API, inferencja; bez torcha)
uv sync
# + trening i ewaluacja (torch CPU, SB3, sb3-contrib, pandas)
uv sync --group train

# testy modułu (bez sieci; około 25 s)
uv run pytest ml/rl_irrigator/tests -q

# API samodzielnie: http://127.0.0.1:8001/docs
cd ml/rl_irrigator && uv run uvicorn kowr_rl.api:app --port 8001
```

Przykładowe zapytanie:

```bash
curl -X POST http://127.0.0.1:8001/rl/recommend -H "Content-Type: application/json" \
  -d '{"parcel_id": "302111_5.0015.72", "crop": "ziemniak", "date": "2024-07-02", "budget_mm": 120}'
```

Pełne przykłady odpowiedzi są w [`examples/`](examples/).

**Integracja z backendem zespołu** (`src/backend/main.py`, Tor C):

```python
from kowr_rl.api import router as rl_router
app.include_router(rl_router)            # /rl/recommend, /rl/compare, /rl/health

# albo bezpośrednio w potoku analizy:
from kowr_rl.inference import recommend, compare_strategies, to_irrigation_advice, to_source_audit_entries
rec = recommend(lat, lon, "kukurydza", date, budget_mm=150, include_history=True)
advice = IrrigationAdvice(**to_irrigation_advice(rec))      # kontrakt z hydro_core.schemas
```

**Odtworzenie danych, treningu i ewaluacji:**

```bash
uv run python ml/rl_irrigator/scripts/fetch_training_data.py     # około 12 min, cache w data/cache
uv run python ml/rl_irrigator/scripts/calibrate_forecast.py      # szum prognozy vs ECMWF 2024
uv run python ml/rl_irrigator/scripts/sanity_baselines.py        # sanity check strategii bazowych
cd ml/rl_irrigator
# zestaw holdout (czysty test E6): 50 nowych punktów, ok. 10 min
uv run python scripts/fetch_training_data.py --out-dir data/holdout --grid-offset 0.35 \
    --exclude-manifest data/MANIFEST.json --min-dist-km 25 --forecast-years 2025 2026
# konfiguracja finalna E6f (3 ziarna, każde ok. 11–14 min na CPU)
for s in 1 2 3; do uv run python -m kowr_rl.train --run E6f_s$s --seed $s --c-w 0.0005 --c-p 0.0003 --lr 0.0003 \
    --ent-coef 0.001 --low-budget-prob 0.3 --teacher fao_small --bc-seasons 8000 --bc-epochs 8 \
    --n-envs 8 --n-steps 1024 --threads 2; done
uv run python scripts/compare_val.py E6f_s1@dom E6f_s2@dom E6f_s3@dom   # wybór modelu (tylko walidacja 2024)
uv run python -m kowr_rl.evaluate --runs E6f_s1@dom E6f_s2@dom E6f_s3@dom --dataset holdout  # reports/evaluation_holdout.md
uv run python -m kowr_rl.evaluate --runs E6f_s1@dom E6f_s2@dom E6f_s3@dom                    # reports/evaluation.md
uv run python scripts/export_policy.py E6f_s2@dom               # artifacts/models/ppo_irrigator_weights.npz
uv run python scripts/make_examples.py                          # examples/*.json
uv run python scripts/benchmark_latency.py                      # reports/latency.json
tensorboard --logdir runs
```

---

## 2. Architektura w 5 zdaniach

1. `data.py` pobiera opad i ET0 (Open-Meteo: archiwum, prognoza ECMWF, archiwalne prognozy ECMWF), kategorię gleby IUNG-PIB i działkę z ULDK. Robi to przez klienta HTTP z cache na dysku, ponawianiem i pauzami.
2. `simulator.py` liczy dobowy bilans FAO-56 („wiadro”: Ks, ETa, perkolacja) i plon względny FAO-33, wektorowo dla tysięcy sezonów naraz. Bilans masy zamyka się dokładnie.
3. `env.py` opakowuje ten sam symulator w środowisko Gymnasium `KowrIrrigationEnv`. Akcje to dawki 0/10/20/30 mm z maską budżetu, a nagroda to −Ky·(ETc−ETa)/ETc_sezon − c_w·I − c_p·DP. `train.py` uczy na nim MaskablePPO na latach 2016–2023 i wybiera model na 2024.
4. `baselines.py` (brak nawadniania, kalendarz, reguła FAO, FAO + prognoza, FAO małe dawki + prognoza, wyrocznia DP) oraz `evaluate.py` porównują strategie na latach testowych 2025–2026 z prawdziwymi prognozami ECMWF i bootstrapem po punktach i latach. Robią to na punktach treningowych i na zestawie holdout z nowymi lokalizacjami.
5. `inference.py` i `api.py` (`/rl/recommend`, `/rl/compare`) odtwarzają sezon działki do wskazanej daty. Domyślnie używają agenta RL w czystym numpy (`artifacts/models/ppo_irrigator_weights.npz`), a na życzenie (`policy=...`) dowolnej reguły bazowej.

Pełne rozbicie, co agent bierze pod uwagę przy decyzji, ze wszystkimi wzorami: [`docs/RL_IRRIGATION_DECISION_MODEL.md`](../../docs/RL_IRRIGATION_DECISION_MODEL.md).

---

## 3. Model i parametry

- **Bilans:** ETc = Kc·ET0, p = p_tab + 0,04·(5 − ETc), RAW = p·TAW. Ks = 1 dla Dr ≤ RAW, w przeciwnym razie (TAW − Dr)/(TAW − RAW). ETa = min(Ks·ETc, dostępna woda). Nadwyżka ponad pojemność polową to perkolacja DP. Przed siewem bilans startuje 1 marca od Dr = 0.
- **Plon:** Ya/Ym = 1 − Ky·(1 − ΣETa/ΣETc), obcięty do [0, 1].
- **Uprawy** (`config/crops.yaml`, źródło przy każdej liczbie: FAO-56 tab. 11, 12, 22 i 24/FAO-33):

| Uprawa | Kc ini/mid/end | Fazy [dni] | Start sezonu | p | Ky |
|---|---|---|---|---|---|
| Kukurydza na ziarno | 0,30 / 1,20 / 0,60 | 30/40/50/50 | 25 IV | 0,55 | 1,25 |
| Ziemniak | 0,50 / 1,15 / 0,75 | 30/35/50/30 | 20 IV | 0,35 | 1,10 |
| Pszenica ozima (od wznowienia wegetacji) | 0,40 / 1,15 / 0,30 | 20/45/40/20 | 25 III | 0,55 | 1,05 |
| Burak cukrowy | 0,35 / 1,20 / 0,70 | 50/40/50/40 | 10 IV | 0,55 | 1,00 |

  Kc end kukurydzy wynosi 0,60, a nie 0,35. FAO podaje 0,60 dla zbioru ziarna o wysokiej wilgotności, co odpowiada praktyce w Polsce. **Wszystkie parametry są oznaczone do weryfikacji z mentorem.**
- **Gleba** (`config/soils.yaml`): kategorie podatności IUNG-PIB I–IV i TAW reprezentatywne 110/150/185/220 mm. Kody 5, 11–14, 19 i 99 dają komunikat „to nie jest grunt orny”, a kody 0 i 255 komunikat „brak danych”, bez rekomendacji.
- **Obserwacja agenta (17 cech, wersja v2 z E6):**
  - postęp sezonu i Kc;
  - Dr/TAW;
  - suma opadu i ET0 z ostatnich 7 dni;
  - prognoza opadu na 1, 2, 3 i 7 dni;
  - budżet pozostały/początkowy oraz pozostały w mm;
  - TAW, p i Ky;
  - (RAW − Dr)/TAW;
  - klimatologiczne ETc do końca sezonu i stosunek pozostałego budżetu do tego ETc.

  Polityki v1 (13 cech, E1–E5) nadal działają, bo czytają pierwsze 13 cech.

  Parametry gleby i uprawy są w obserwacji, więc **jedna polityka obsługuje wszystkie gleby i uprawy**.
- **Prognoza w treningu** jest zaszumiona i skalibrowana na prawdziwych prognozach ECMWF z 2024 roku (EXPERIMENTS.md, E0). W walidacji i teście agent dostaje prawdziwe archiwalne prognozy ECMWF.
- **Trening finalnego agenta (E6f):**
  - start od klonu reguły „małe dawki + prognoza” (10 mm przy Dr > 0,9·RAW, pominięcie przy prognozie ≥ 5 mm na 2 dni);
  - potem MaskablePPO z nagrodą `c_w` = 5e-4 i `c_p` = 3e-4;
  - 30% epizodów z ciasnym budżetem 20–100 mm;
  - wybór checkpointu, który na walidacji 2024 ma plon ≥ FAO i wodę ≤ FAO przy każdym budżecie.

---

## 4. Źródła danych i licencje

| Źródło | Do czego | Tryb | Licencja / warunki |
|---|---|---|---|
| Open-Meteo Historical Weather API (`archive-api.open-meteo.com`, model `best_match`: ERA5/ERA5-Land/ECMWF IFS) | dobowy opad i ET0 FAO-56 od 2016 | LIVE (inferencja) + PRE-FETCHED (trening) | CC BY 4.0; darmowe API do użytku niekomercyjnego, limity dzienne |
| Open-Meteo Forecast API (`api.open-meteo.com`, `ecmwf_ifs025`) | prognoza na dziś i ostatnie dni (opóźnienie archiwum) | LIVE | CC BY 4.0 (dane ECMWF open data, CC BY 4.0) |
| Open-Meteo Previous Runs API (`previous-runs-api.open-meteo.com`, `ecmwf_ifs025`) | archiwalne prognozy opadu z wyprzedzeniem 1–7 dni (od 2024): kalibracja, walidacja, test, rekomendacje dla dat historycznych | LIVE + PRE-FETCHED | CC BY 4.0 |
| IUNG-PIB WMS `Susza:katgl_landuse2024` (`sgeo.iung.pl`) | kategoria podatności gleby na suszę i użytkowanie terenu w punkcie | LIVE + PRE-FETCHED (wybór punktów) | publiczna usługa IUNG-PIB (System Monitoringu Suszy Rolniczej) |
| GUGiK ULDK (`uldk.gugik.gov.pl`) | geometria działki po numerze TERYT; województwo punktów treningowych | LIVE + PRE-FETCHED | dane publiczne (ustawa Prawo geodezyjne i kartograficzne) |

Do zewnętrznych serwisów wysyłane są wyłącznie współrzędne punktu lub numer działki. Nie są przetwarzane żadne dane osobowe.

### Które dane pobrano wcześniej i kiedy

| Dane | Tryb | Kiedy pobrano | Zakres |
|---|---|---|---|
| Pogoda działki (opad, ET0), kategoria gleby IUNG, działka ULDK, prognoza ECMWF lub archiwalna prognoza dla daty analizy | **LIVE** przy każdym zapytaniu `recommend` / `compare` (cache na dysku: archiwum przeszłości bez wygasania, dane z ostatnich 10 dni przez 6 h, prognoza przez 3 h) | w chwili zapytania; data pobrania jest w polu `sources[].retrieved_at` odpowiedzi | 2016-01-01 → wczoraj, prognoza do 15 dni |
| Wybór 50 punktów treningowych: siatka 0,7°, warstwa IUNG `katgl_landuse2024` | PRE-FETCHED | 21.11.2026, 12:36–12:37 UTC | 135 węzłów → 83 rolne → 50 (wszystkie 16 województw) |
| Pogoda punktów treningowych (Open-Meteo archive) | PRE-FETCHED | 21.11.2026, 12:37–12:48 UTC | 2016-01-01 → 2026-11-16, 0 brakujących dni |
| Archiwalne prognozy ECMWF punktów (Previous Runs) | PRE-FETCHED | 21.11.2026, 12:37–12:48 UTC | 15 III – 31 X w latach 2024, 2025, 2026 (0,25% pustych komórek) |
| Zestaw holdout (czysty test E6): siatka przesunięta o 0,35°, IUNG, pogoda i archiwalne prognozy ECMWF ([`data/holdout/MANIFEST.json`](data/holdout/MANIFEST.json)) | PRE-FETCHED | 21.11.2026, 15:17–15:26 UTC | 112 węzłów → 81 rolnych → 50 (16 województw, ≥ 35 km od punktów treningowych); pogoda 2016-01-01 → 2026-11-16; prognozy 15 III – 31 X 2025 i 2026 |
| Wagi polityki RL `artifacts/models/ppo_irrigator_weights.npz` (E6f_s2@dom) | wytrenowane offline na danych PRE-FETCHED | 21.11.2026 | trening 2016–2023, walidacja 2024, test 2025–2026 (holdout + punkty treningowe) |

Szczegóły (URL, znaczniki czasu dla każdego punktu, kontrola jakości) są w [`data/MANIFEST.json`](data/MANIFEST.json). Pobrane dane (pogoda i archiwalne prognozy, pliki `.parquet`, ok. 6 MB) są w repozytorium w `data/weather`, `data/forecasts` i `data/holdout/`. Można je odtworzyć skryptem `scripts/fetch_training_data.py`. **Rekomendacja zawsze liczy się na żywo z danych działki.** Dane pobrane wcześniej służą wyłącznie do treningu i ewaluacji.

---

## 5. Biblioteki i narzędzia

| Biblioteka | Wersja | Licencja | Użycie |
|---|---|---|---|
| NumPy | 2.2.6 | BSD-3-Clause | symulator, polityka runtime |
| httpx | 0.28.1 | BSD-3-Clause | klient HTTP |
| PyYAML | 6.0.4 | MIT | konfiguracja |
| FastAPI / Starlette | 0.165.0 / 1.10.0 | MIT / BSD-3-Clause | API |
| Pydantic | 2.14.4 | MIT | walidacja zapytań |
| Uvicorn | 0.54.0 | BSD-3-Clause | serwer API |
| Shapely | 2.2.0 | BSD-3-Clause | centroid działki |
| pandas / PyArrow | 3.1.1 / 26.0.1 | BSD-3-Clause / Apache-2.0 | zbiór treningowy (parquet) |
| PyTorch (CPU) | 2.15.0 | BSD-3-Clause | trening (tylko grupa `train`) |
| Stable-Baselines3 / sb3-contrib | 2.9.2 | MIT | PPO / MaskablePPO |
| Gymnasium | 1.4.0 | MIT | środowisko RL |
| TensorBoard | 2.21.0 | Apache-2.0 | logi treningu |
| Matplotlib | 3.11.2 | licencja Matplotlib (PSF-podobna, permisywna) | wykresy raportu |
| pytest | 9.2.0 | MIT | testy |

**Modele:** jedna polityka MaskablePPO (MLP [128, 128], tanh) wytrenowana od zera na otwartych danych. W runtime działa jako pliki `.npz` w czystym numpy. Nie używamy modeli zewnętrznych ani LLM w obliczeniach.
**Narzędzia AI przy wytwarzaniu:** kod, testy i dokumentację tego modułu przygotowano z pomocą asystenta Claude Code (Anthropic). Asystent nie jest wywoływany w działającej aplikacji.

---

## 6. Wyniki

**Czysty test: 50 nowych lokalizacji (holdout), lata 2025–2026** (pełny raport: [`reports/evaluation_holdout.md`](reports/evaluation_holdout.md)).
- **Układ:** 50 punktów × 2 lata × 4 uprawy × 4 gleby × 4 budżety (50/100/150/250 mm), czyli 6400 sezonów na strategię.
- **Dane:** wszystkie strategie dostają te same dane z **prawdziwymi prognozami ECMWF**.
- **Przedziały:** 95% bootstrap po punktach i latach.
- **Niezależność:** lokalizacje nie były używane ani w treningu, ani przy wyborze modelu.

| Strategia | Plon względny | Woda [mm/sezon] | Perkolacja [mm] | Dni stresu |
|---|---|---|---|---|
| Bez nawadniania | 0,845 | 0 | 36 | 49 |
| Kalendarz | 0,951 | 138 | 101 | 21 |
| Reguła FAO-56 | 0,967 | 87 | 41 | 16 |
| FAO-56 + prognoza | 0,967 | 84 | 40 | 16 |
| FAO-56 małe dawki + prognoza | 0,969 | 83 | 38 | 16 |
| **Agent RL (E6f_s2@dom, domyślny)** | **0,970** | **81** | 38 | 15 |
| Wyrocznia (znana pogoda, górna granica) | 0,972 | 76 | 37 | 13 |

| Budżet | ΔPlon RL − FAO [pkt proc.] | ΔWoda RL − FAO [mm] |
|---|---|---|
| łącznie | **+0,23** [+0,14; +0,33] | **−6,0** [−7,3; −5,1] |
| 50 mm | +0,12 [+0,07; +0,17] | −0,9 [−1,4; −0,5] |
| 100 mm | +0,26 [+0,12; +0,43] | −3,9 [−5,2; −2,8] |
| 150 mm | +0,28 [+0,13; +0,44] | −8,2 [−10,0; −6,5] |
| 250 mm | +0,27 [+0,20; +0,33] | −11,2 [−13,2; −9,6] |

- **Przy każdym budżecie** agent ma istotnie wyższy plon i istotnie mniejsze zużycie wody niż reguła FAO. Według kryterium ustalonego przed testem to werdykt **„gotowy”**.
- **Wobec najlepszej prostej reguły** („FAO małe dawki + prognoza”): +0,11 pkt proc. i −2,0 mm, istotnie przy każdym budżecie. **Wobec FAO + prognoza:** +0,27 pkt proc. i −2,7 mm.
- **Górna granica:** agent zbiera ok. 55–60% przewagi, jaką nad FAO ma wyrocznia znająca przyszłą pogodę, jednocześnie w plonie i w wodzie.
- **Te same lata na punktach treningowych** ([`reports/evaluation.md`](reports/evaluation.md)): +0,23 pkt proc. i −5,6 mm, także istotnie przy każdym budżecie.
- **Stabilność:** 3 ziarna na holdoucie dają plon 0,969–0,970 i wodę 81–83 mm.
- **Jak to osiągnęliśmy:** nowe cechy obserwacji, start od reguły małych dawek i wybór modelu, który na walidacji wygrywa z FAO przy każdym budżecie. Całą drogę opisuje [`EXPERIMENTS.md`](EXPERIMENTS.md).

**Czas odpowiedzi** (`reports/latency.json`, 10 losowych punktów i dat z lat 2016–2026, tempo pojedynczego użytkownika, 21.11.2026):
- **z pobraniem danych:** mediana **0,48 s**, maksimum **4,9 s** (przypadek z chwilowym timeoutem połączenia i ponowieniem); cel < 10 s;
- **bez sieci (cache):** mediana **0,011 s**, maksimum **0,05 s**; cel < 1 s.

Wcześniejszy pomiar dał 11,4 s (timeout WMS IUNG przy 8 s limicie) i 17,5 s (czekanie na limit Open-Meteo). Po tym zaostrzyłem limity runtime: 4 s na zapytanie, jedno ponowienie, brak czekania dłuższego niż 2 s.

---

## 7. Zakres i założenia modelu

- **Bilans wody:** standardowy model FAO-56 strefy korzeniowej, liczony dobowo. Plon względny wg FAO-33 to wskaźnik straty plonu z niedoboru wody.
- **Pogoda:** Open-Meteo (reanaliza i ECMWF IFS) dla współrzędnych działki. Prognozy archiwalne ECMWF są dostępne od 2024 r., a dla starszych dat moduł używa klimatologii punktu.
- **Gleba:** pojemność wodna (TAW) z kategorii podatności IUNG-PIB (`config/soils.yaml`).
- **Uprawy:** parametry z FAO-56/33 (`config/crops.yaml`). Łatwo je podmienić na wartości eksperckie.
- **Historia nawodnień:** rolnik może ją podać (`irrigation_history`). Bez niej moduł rozkłada zużytą część budżetu zgodnie ze strategią.
- **Skalowanie:** zimna analiza pobiera 10 lat archiwum (ok. 57 „wywołań” Open-Meteo). Kolejne zapytania dla tego samego punktu idą z cache, a przy limicie API moduł od razu zwraca czytelny komunikat. Przy wielu użytkownikach zalecana jest własna instancja Open-Meteo albo licencja komercyjna (por. główne README, sekcja 8).

---

## 8. Jak zweryfikować wyniki

1. `uv run pytest ml/rl_irrigator/tests -q` sprawdza bez sieci:
   - bilans masy z dokładnością 1e-9 na 200 tys. losowych kroków;
   - zakresy Ks i plonu oraz przypadki brzegowe;
   - `check_env`;
   - identyczność środowiska i symulatora;
   - identyczność decyzji polityki `.npz` i SB3;
   - wyrocznię jako górną granicę;
   - API i kontrakty `IrrigationAdvice` / `SourceAuditEntry`.
2. `reports/forecast_calibration.json` porównuje szum treningowy z prawdziwymi prognozami ECMWF 2024.
3. `reports/evaluation_holdout.md` (czysty test na nowych lokalizacjach) i `reports/evaluation.md` (punkty treningowe) zawierają tabele, wykresy, przedziały bootstrap, przykładowe sezony i werdykt. Porównanie kandydatów na walidacji, na podstawie którego wybrano model, jest w `reports/e6_val_selection.json`.
4. Wynik czystego testu da się odtworzyć bez treningu, na wdrożonym modelu z `models/E6f_s2` i danych z repozytorium: `uv run python -m kowr_rl.evaluate --runs E6f_s2@dom --dataset holdout`.
5. `EXPERIMENTS.md` to dziennik wszystkich zmian nagrody i hiperparametrów z wynikami walidacji.
6. `compare_strategies()` liczy dla konkretnej działki ostatnie 10 sezonów wszystkimi strategiami na tych samych danych.
7. Każda odpowiedź zawiera `sources`, czyli URL, datę pobrania i tryb live/cache, więc liczby można sprawdzić u źródła.
