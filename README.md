# AgroRetencja KOWR — Cyfrowy Asystent Retencji i Bilansu Wodnego

> **Zadanie:** HackNation 2026  KOWR: *Zatrzymaj wodę, chroń plony – Inteligentne wsparcie gospodarowania wodą w rolnictwie*  
> **Repozytorium:** Prywatne repozytorium konkursowe (kod pisany od zera zgodnie z § 5 Regulaminu KOWR)

---

## 1. Instrukcja uruchomienia rozwiązania

### Wymagania wstępne
- Python `3.11`–`3.12` oraz menedżer pakietów [`uv`](https://docs.astral.sh/uv/)
- Node.js `20+` oraz [`pnpm`](https://pnpm.io/)
- Biblioteki systemowe `WeasyPrint` (Cairo, Pango) do eksportu raportów PDF

### Uruchomienie lokalne (Backend + Frontend)

```bash
# 1. Instalacja srodowiska CPU runtime
uv sync

# 2. Uruchomienie API FastAPI
uv run uvicorn backend.main:app --host 0.0.0.0 --port 8000

# 3. Instalacja i uruchomienie interfejsu webowego
cd frontend
pnpm install
pnpm dev
```

Aplikacja uruchamia się w przeglądarkach systemu Windows (Chrome, Firefox, Edge, Opera, Safari) bez instalowania dodatkowego oprogramowania przez użytkownika.

### Opcjonalny trening modeli ML (GPU / CPU)

```bash
uv sync --group train
```

---

## 2. Opis podstawowych funkcjonalności

System wspiera rolnika bez specjalistycznej wiedzy GIS w podejmowaniu decyzji wodnych dla dowolnej działki ewidencyjnej w Polsce i dowolnej daty z ostatnich 10 lat (2016–2026) w ścisłej kolejności:

1. **Ocena sytuacji wodnej:** Wylicza bieżący bilans wodny strefy korzeniowej (**FAO-56**), Klimatyczny Bilans Wodny (**IUNG-PIB KBW**) oraz 10-letni profil podatności działki na suszę. Wynik ma postać dwuosiową: `WaterScore (0–100)` oraz `WaterRisk` z mechanizmem absolutnego weta.
2. **Identyfikacja przyczyn niedoboru wody:** Rozbija problem na 4 grupy czynników: klimat/opad vs $ET_0$, pojemność retencyjna gleby, spływ powierzchniowy z NMT 1 m oraz faza rozwojowa uprawy (`TreeSHAP` + bilans fizyczny).
3. **Rekomendacje retencyjne i agrotechniczne (*retain water where it falls*):** Wskazuje na mapie działki strefy spływu i akumulacji wody (`TWI`) oraz proponuje ranking zabiegów krajobrazowych i glebowych z symulatorem **„Co jeśli”** opartym na plonowaniu **FAO-33**.
4. **Rekomendacje nawadniania (opcjonalnie):** Wylicza zasadność, dawkę i okno zabiegu (reguła FAO-56 oraz agent RL `FAO-RL-Irrigator`), pokazując 10-letnie porównanie strategii.
5. **Audytowalny raport PDF:** Eksportuje 2-stronicowy dokument zawierający diagnozę, mapę działań, dokładne źródła z datami oraz jawną sekcję niepewności i braków danych.

---

## 3. Skrócony opis architektury rozwiązania

Architektura stosuje zasadę **Explainability by Design**: warstwa percepcji danych i lekkie modele ML są ściśle oddzielone od deterministycznego kalkulatora fizycznego FAO-56/33. Szczegółowa specyfikacja znajduje się w [docs/SOLUTION_BLUEPRINT.md](docs/SOLUTION_BLUEPRINT.md) oraz [docs/IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md).

- **`src/hydro_core/`:** Wspólne kontrakty Pydantic v2, symulator bilansu FAO-56/33, przetwarzanie NMT 1 m (spadki, spływ D8, TWI), wyliczanie `WaterScore`, reguł weta `WaterRisk` i `DataConfidence (AoA)`.
- **`src/backend/`:** Asynchroniczne API FastAPI (`asyncio.gather`) odpytujące równolegle 5 kategorii źródeł (budżet czasu `≤ 30 s`, cel `10–18 s`), lekka inferencja CPU (`LightGBM` + eksportowane wagi NumPy `.npz`) oraz generator raportów PDF.
- **`ml/`:** Skrypty pobierania otwartych danych z Polski oraz treningu modeli `HydroFusion-PL` (rekonstrukcja pod chmurami + TreeSHAP) i `FAO-RL-Irrigator` (PPO).
- **`frontend/`:** Aplikacja React + TypeScript + Tailwind + MapLibre GL JS (karta decyzji + mapa kontekstowa z 3 widokami).

---

## 4. Wykorzystane źródła i zakresy danych

System integruje wszystkie 5 wymaganych kategorii bezpłatnych danych otwartych (okno 10 lat: 2016–2026, obszar całej Polski):

1. **Dane przestrzenne i NMT:** `ULDK GUGiK` (granice działek ewidencyjnych TERYT), `Geoportal WCS NMT GRID1` (wysokość 1 m), `0_dzialki.parquet` (EGiB).
2. **Dane meteorologiczne:** `Open-Meteo Historical & Forecast API` (10-letnia seria dzienna opadu, $ET_0$ FAO-56, temperatury) oraz `IMGW-PIB Dane Publiczne API` (bieżące dane synoptyczne).
3. **Dane satelitarne:** `Sentinel-2 L2A` z katalogu `Earth Search AWS STAC` (pasma `B04, B08, B8A, B11, SCL` w rozdzielczości 10–20 m).
4. **Dane glebowo-rolnicze:** `ISRIC SoilGrids v2.0` (`sand`, `clay`, `soc`, gęstość) oraz progi 4 kategorii podatności gleb na suszę wg metodyki `IUNG-PIB`.
5. **Dane hydrologiczne i tło suszowe:** `Geoportal BDOT10k WMS` / lokalnie buforowana sieć hydrograficzna oraz `European Drought Observatory (EDO) WMS`.

---

## 5. Podział danych: `LIVE` vs `PRE-FETCHED` (z datami pobrania)

| Źródło | Tryb w prototypie | Zakres i data pobrania (cache) |
| :--- | :--- | :--- |
| `ULDK GUGiK` | **LIVE** *(fallback: pre-fetched)* | Pobierane na żywo dla wskazanego numeru działki lub współrzędnych; awaryjny indeks `0_dzialki.parquet` *(planowana data snapshotu: 21.11.2026)*. |
| `Geoportal NMT 1 m WCS` | **LIVE** *(fallback: pre-fetched)* | Wycinek NMT 1 m pobierany na żywo dla bboxa działki; awaryjnie Copernicus DEM 30 m *(planowana data snapshotu: 21.11.2026)*. |
| `Open-Meteo` & `IMGW-PIB` | **LIVE** | 10-letnia seria dzienna i 7-dniowa prognoza pobierane na bieżąco dla centroidu działki i daty analizy. |
| `Sentinel-2 L2A (Earth Search)` | **LIVE** | Wyszukiwanie STAC i odczyt COG HTTP Range dla okna `[t - 45 dni, t]` w momencie analizy. |
| `ISRIC SoilGrids` & `IUNG-PIB` | **PRE-FETCHED + LIVE** | Zbuforowana siatka Polski i tabele progowe IUNG *(planowana data: 21.11.2026)* + zapytanie live z cachem SQLite. |
| `BDOT10k / Hydrografia` & `EDO WMS` | **PRE-FETCHED + LIVE** | Warstwa cieków buforowana lokalnie *(21.11.2026)* + aktualna warstwa EDO WMS dla daty $t$. |
| Zbiór treningowy `HydroFusion-PL` / `PPO` | **PRE-FETCHED** | Dane archiwalne 2016–2026 pobrane w dniu `21.11.2026` wyłącznie do treningu wag offline w `artifacts/models/`. |

---

## 6. Wykaz bibliotek, komponentów, modeli ML, usług zewnętrznych i narzędzi AI

- **Backend & Geo:** Python 3.11, FastAPI, Uvicorn, Pydantic v2, HTTPX, NumPy, SciPy, Shapely, PyProj, Rasterio/GDAL, Jinja2, WeasyPrint, Pillow, Matplotlib.
- **Modele ML (wytrenowane na otwartych danych):**
  - `HydroFusion-PL`: LightGBM Multi-Quantile ($\tau \in \{0.05, 0.50, 0.95\}$) + Split Conformal Prediction + natywny TreeSHAP + detektor Area of Applicability ($DI$).
  - `FAO-RL-Irrigator`: Polityka PPO (`stable-baselines3` w fazie treningu, eksport do czystego NumPy `.npz` w runtime).
- **Frontend:** React, TypeScript, Vite, TailwindCSS, MapLibre GL JS.
- **Usługi zewnętrzne:** `uldk.gugik.gov.pl`, `mapy.geoportal.gov.pl`, `archive-api.open-meteo.com`, `api.open-meteo.com`, `danepubliczne.imgw.pl`, `earth-search.aws.element84.com`, `rest.isric.org`, `drought.emergency.copernicus.eu`.
- **Narzędzia AI wspomagające wytwarzanie (nieuruchamiane w kalkulatorze rolnika):** Narzędzia asystenckie wykorzystane wyłącznie przy projektowaniu i pisaniu kodu (do uzupełnienia dokładnymi nazwami przy oddaniu).

---

## 7. Licencje i warunki korzystania

- **Oprogramowanie własne:** Wytworzony kod i dokumentacja podlegają przekazaniu autorskich praw majątkowych na rzecz KOWR zgodnie z § 5 Regulaminu Zadania Konkursowego.
- **Biblioteki Open Source:** Wyłącznie licencje permisywne/kompatybilne (MIT, BSD-3-Clause, Apache-2.0; `WeasyPrint` na BSD-3-Clause, `LightGBM` na MIT).
- **Dane otwarte:**
  - GUGiK / BDOT10k / ULDK / NMT: otwarte dane publiczne (Ustawa Prawo geodezyjne i kartograficzne).
  - Sentinel-2 & EDO Copernicus: EU Copernicus Open Data / JRC Open Data Policy (CC BY 4.0).
  - Open-Meteo (ERA5): CC BY 4.0 (użytek niekomercyjny/testowy na darmowym API; przy skali produkcyjnej KOWR zalecana własna instancja lub licencja komercyjna/IMGW).
  - ISRIC SoilGrids v2.0: CC BY 4.0.
  - IMGW-PIB: otwarte dane publiczne zgodnie z regulaminem udostępniania IMGW-PIB.

---

## 8. Znane ograniczenia techniczne i plan skalowania na całą Polskę

### Znane ograniczenia
- **Rozdzielczość źródeł regionalnych:** `SoilGrids` (250 m) oraz `Open-Meteo` (~9 km) mają grubszą siatkę niż małe działki rolne (`< 0,5 ha`); raport ostrzega o tym jawnie bez pozornej precyzji.
- **Brak danych podziemnych i agrotechnicznych:** System nie zna lokalnego poziomu zwierciadła wód gruntowych, stanu podziemnego drenażu rurkowego ani wykonanych wcześniej zabiegów bez deklaracji rolnika.
- **Zachmurzenie optyczne:** W przypadku gęstych chmur Sentinel-2 odtwarza indeks `NDMI` model `HydroFusion-PL` z jawnym przedziałem niepewności; dla obserwacji poza AoA działa fallback na bilans meteo-glebowo-terenowy.

### Plan skalowania (1,4 mln ha Zasobu WRSP KOWR / cała Polska)
- **Cache warstwowy L1/L2/L3:** Buforowanie par `(id_dzialki, data)` w bazie PostGIS/Redis (`< 0,3 s` dla powtórnych zapytań) oraz współdzielenie kafli Sentinel-2 (MGRS) i siatki meteo na poziomie gminy ogranicza wywołania zewnętrznych API o ok. 98%.
- **Aktualizacja nocna:** Kolejka zadań (`ARQ`/`Celery`) pobiera raz na dobę nowe przeloty satelitarne oraz dobowe sumy opadu i $ET_0$.
- **Szacowany koszt infrastruktury:** Dzięki lekkim modelom CPU (`< 50 ms`/działkę) i otwartym danym pojedynczy serwer aplikacyjny (8 vCPU, 32 GB RAM, NVMe) kosztuje ok. `350–500 zł/mies.` i obsługuje `> 50 000` analiz dziennie (przy ruchu ogólnopolskim wymagana komercyjna/instytucjonalna umowa dla API pogodowego lub własne przetwarzanie danych IMGW-PIB).

---

## 9. Sposób weryfikacji wyników i metryki walidacji

*(Sekcja zostanie uzupełniona twardymi wynikami po zakończeniu treningów w Fazie 3 i benchmarku 20 losowych działek w Fazie 2.)*

1. **Testy jednostkowe fizyki i reguł:** Weryfikacja deterministycznego bilansu FAO-56/33, progów IUNG-PIB oraz reguł weta (`pytest tests/`).
2. **Walidacja przestrzenno-czasowa `HydroFusion-PL`:** Holdout na 2 województwach oraz 2 latach ekstremalnych susz (2018 i 2023) — raportowane metryki: `RMSE`, `MAE`, `R²`, `PICP (90% Conformal)` oraz zgodność z progami `IUNG KBW`.
3. **Walidacja czasowa `FAO-RL-Irrigator`:** Trening na latach `2016–2023`, ewaluacja na `2024–2025` względem strategii bazowych (`Bez nawadniania` vs `Reguła progowa FAO-56` vs `PPO`).
4. **Test obciążeniowy i czasowy:** Weryfikacja czasu odpowiedzi `≤ 30 s` na 20 losowych działkach ewidencyjnych z całej Polski i losowych datach z lat 2016–2026.
