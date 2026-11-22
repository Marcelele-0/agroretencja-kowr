# Rusałka: asystent wody dla działki rolnej

> **Zespół:** SolvronGen Racing In Space
> **Tytuł rozwiązania:** Rusałka – asystent wody dla działki rolnej
> **Zadanie:** HackNation 2026, KOWR: *Zatrzymaj wodę, chroń plony – Inteligentne wsparcie gospodarowania wodą w rolnictwie*
> **Repozytorium:** prywatne repozytorium konkursowe. Kod jest pisany od zera zgodnie z § 5 Regulaminu KOWR.
> **Prezentacja (PDF) i film z działania prototypu:** załączone w formularzu zgłoszeniowym na Platformie konkursowej.
> **Dokumentacja:**
> - [docs/SOLUTION_BLUEPRINT.md](docs/SOLUTION_BLUEPRINT.md): koncepcja i metoda;
> - [docs/IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md): plan i stan prac;
> - [docs/BACKEND_ARCHITECTURE.md](docs/BACKEND_ARCHITECTURE.md): architektura backendu i rejestry rozszerzeń;
> - [docs/RL_IRRIGATION_DECISION_MODEL.md](docs/RL_IRRIGATION_DECISION_MODEL.md): model decyzji nawadniania (PPO vs FAO-56);
> - [frontend/README.md](frontend/README.md): aplikacja webowa;
> - [reports/](reports/): przykładowe raporty PDF wygenerowane przez prototyp.

Rusałka pokazuje stan wody na dowolnej działce rolnej w Polsce, dla dowolnej daty z ostatnich 10 lat. Wynik zawiera:
- ocenę sytuacji wodnej (zapas wody w glebie, susza według progów IUNG-PIB, historia 10 lat);
- przyczyny niedoboru wody;
- działania retencyjne i agrotechniczne z symulacją „co jeśli”;
- poradę o nawadnianiu (dawka na dziś i plan na 14 dni).

Wzorem jest System Monitoringu Suszy Rolniczej IUNG-PIB, liczony na poziomie jednej działki (ustalenie z mentorami KOWR, 21.11.2026).

**Problem.** Dane satelitarne, meteorologiczne, glebowe i hydrologiczne są otwarte, ale rozproszone i trudne dla rolnika.
**Rozwiązanie.** Rolnik podaje numer działki (albo klika na mapie), uprawę i datę. W ok. 5–16 s dostaje ocenę wody liczoną z danych pobranych w tej chwili, przyczyny niedoboru, działania retencyjne posortowane według efektu na tej działce i poradę o nawadnianiu, a na końcu raport PDF ze źródłami i ograniczeniami.

---

## 1. Instrukcja uruchomienia rozwiązania

### Wymagania
- Node.js 20+ i [`pnpm`](https://pnpm.io/) (frontend). Jeśli `pnpm` nie jest zainstalowany: `corepack enable` (Node 20+ ma corepack) albo `npm install -g pnpm`.
- Python 3.11 i [`uv`](https://docs.astral.sh/uv/) (backend i moduły ML).
- System: Windows, Linux lub macOS. Rolnik potrzebuje tylko przeglądarki.

### Krok 1: backend (FastAPI, port 8000)

```bash
uv sync
uv run uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

Sprawdzenie: `http://127.0.0.1:8000/api/health` zwraca `"status": "ok"` i listę 15 upraw.

### Krok 2: aplikacja webowa (frontend, w drugim terminalu)

```bash
cd frontend
pnpm install
pnpm build
pnpm preview      # http://localhost:4173
```

Domyślnie frontend łączy się z backendem przez pośrednika Vite (`/api`, `/rl` → `127.0.0.1:8000`), więc nie trzeba tworzyć pliku `frontend/.env`. Inny adres backendu ustawia `VITE_API_URL` (wzór w [frontend/.env.example](frontend/.env.example)). `VITE_API_URL=` (puste) wyłącza backend: zostaje tylko uproszczona ocena IUNG liczona w przeglądarce.

Tryb deweloperski: `pnpm dev` (http://localhost:5173). Aplikacja działa w przeglądarkach Chrome, Edge, Firefox, Safari i Opera oraz na telefonie. Użytkownik nic nie instaluje. Szczegóły: [frontend/README.md](frontend/README.md).

### Raport PDF na Windows
Backend próbuje kolejno: WeasyPrint (pełny układ, wymaga bibliotek GTK/Pango), Matplotlib (ta sama treść w prostszym układzie, działa na każdym Windows), HTML. Nagłówek `X-Report-Renderer` mówi, który renderer zadziałał.

### Moduł nawadniania RL

Moduł `kowr_rl` (`ml/rl_irrigator`) jest podpięty pod API FastAPI (`POST /rl/recommend`, `POST /rl/compare`) i używany w kroku 4 analizy (`src/backend/irrigation.py`). Obsługuje wszystkie 15 upraw. W analizie agent startuje z zapasu wody w glebie policzonego w kroku 1, więc ocena i nawadnianie pokazują tę samą liczbę. Z `plan_days=14` daje plan na 14 dni z prognozy zespołowej ECMWF (51 wariantów). Opis decyzji: [docs/RL_IRRIGATION_DECISION_MODEL.md](docs/RL_IRRIGATION_DECISION_MODEL.md), prognoza 14-dniowa: [ml/rl_irrigator/reports/forecast14.md](ml/rl_irrigator/reports/forecast14.md).

---

## 2. Opis podstawowych funkcjonalności

1. **Wybór działek na mapie.**
   - Ortofotomapa GUGiK z granicami działek ewidencyjnych.
   - Działkę dodajesz kliknięciem, numerem TERYT albo nazwą obrębu i numerem. Możesz wybrać kilka i dla każdej ustawić uprawę.
   - Dla działki nierolniczej (las, woda, zabudowa wg mapy IUNG-PIB) serwis pokazuje komunikat zamiast analizy.
2. **Ocena sytuacji wodnej** dla wybranej daty (dziś albo dowolny dzień od 1.01.2016):
   - stan wody słownie i na skali od −5 (susza skrajna) przez 0 (optimum) do +5 (zalanie). Strona sucha wynika z zapasu wody w strefie korzeniowej (bilans FAO-56), poprawionego o bardzo niski bilans opad − parowanie z 60 dni i spadek wilgotności łanu z satelity; strona mokra z przesiąku i spływu z ostatnich 14–30 dni oraz dodatniego bilansu z 60 dni. Ryzyko nie jest niższe, niż wskazuje stan wody;
   - ryzyko (z wetem: susza wg progów IUNG-PIB, głęboki niedobór w fazie wrażliwej) i pewność danych;
   - ogólna ocena warunków wodnych 0–100 (WaterScore: zapas wody, KBW, satelita NDMI, pojemność gleby, teren);
   - zdjęcie satelitarne czytane przez uprawę: waga NDMI zależy od fazy uprawy i pokrycia gleby łanem w dniu zdjęcia (FAO-56, wzór 76). Poza sezonem uprawy (goła gleba, ściernisko, spoczynek zimowy) NDMI nie wchodzi do oceny, a raport mówi dlaczego; w sadach, krzewach, chmielu, truskawkach, warzywach i na łąkach waga jest ograniczona, bo piksel 10–20 m łączy rośliny z międzyrzędziem lub łąka jest koszona;
   - wykres stanu dzień po dniu z prognozą na kolejne dni;
   - 14 okien klimatycznego bilansu wodnego (KBW) z progami IUNG dla uprawy i kategorii gleby;
   - ten sam dzień w każdym z 10 poprzednich lat.
3. **Przyczyny niedoboru wody:** udział klimatu, gleby, spływu i uprawy, policzony scenariuszami „co gdyby” na tym samym bilansie. Gdy niedoboru nie ma, serwis mówi to wprost i nie podaje procentów.
4. **Działania i symulacja „co jeśli”.**
   - Lista działań jest posortowana według zysku wody na tej działce w najsuchszym sezonie z 10 lat.
   - Działania na urządzeniach wodnych mają oznaczenie „Wymaga zgody Wód Polskich”.
   - Dla upraw wieloletnich (sady, krzewy, chmiel, truskawki) i łąk serwis nie proponuje międzyplonu ani uprawy bezorkowej.
5. **Nawadnianie** (wszystkie uprawy, w sezonie): dawka na dziś, plan na 14 dni z szansą podlewania w każdym dniu oraz porównanie strategii z 10 sezonów. Poza sezonem: tylko porównanie strategii.
6. **Raport:** tabela źródeł z czasem pobrania i trybem (na żywo / bufor / zapas / brak danych), ograniczenia i niepewność. Przycisk „Pobierz raport PDF” pobiera PDF z serwera (bez serwera drukuje stronę).

---

## 3. Skrócony opis architektury rozwiązania

```text
Przeglądarka (frontend/, React + MapLibre)
  ├─ ULDK, KIEG, ortofotomapa GUGiK, IUNG WMS, Open-Meteo (pośrednik /om; przy limicie dane z backendu: /api/weather/daily z zapasem NASA POWER)
  ├─ src/analysis/local.ts: KBW w oknach IUNG (ocena uproszczona, gdy serwer nie odpowiada)
  └─ src/api/: podpięcia do backendu (domyślnie ten sam adres, pośrednik Vite)
Backend (src/backend, FastAPI)        → POST /api/analyze, POST /api/what-if, GET /api/report.pdf, GET /api/registries
  ├─ clients/: 8 źródeł pobieranych równolegle, twardy limit 25 s, bufor SQLite
  ├─ src/hydro_core: bilans FAO-56 + SCS-CN, stan wody, WaterScore, weta IUNG, przyczyny, retencja, porady
  ├─ inference/hydrofusion.py: HydroFusion-PL (LightGBM) – NDMI pod chmurami z przedziałem 90%
  └─ ml/rl_irrigator (kowr_rl)        → POST /rl/recommend, POST /rl/compare, krok 4 analizy
```

Architekturę backendu (rejestry rozszerzeń, jedna oś czasu historii i prognozy, decyzje ADR) opisuje [docs/BACKEND_ARCHITECTURE.md](docs/BACKEND_ARCHITECTURE.md).

- Frontend nie używa makiet. Każda liczba pochodzi z obliczenia na danych pobranych dla tej działki i daty.
- Gdy backend odpowiada, jego wynik zastępuje obliczenie przeglądarki. Kontrakt jest w `frontend/src/api/contract.ts`.

---

## 4. Wykorzystane źródła i zakresy danych

| Kategoria | Źródło | Zakres | Gdzie |
| --- | --- | --- | --- |
| Przestrzenne | GUGiK ULDK | granica działki, gmina, obręb | frontend, backend, RL |
| Przestrzenne | GUGiK ortofotomapa (WMTS i WMS), KIEG (WMS) | podkład mapy, granice i numery działek | frontend |
| Glebowo-rolnicze | IUNG-PIB WMS `Susza:katgl_landuse2024` | kategoria gleby I–IV, użytkowanie (odmowa dla lasu, wody, zabudowy) | frontend, backend, RL |
| Glebowo-rolnicze | IUNG-PIB progi SMSR (Rozp. MRiRW z 11.04.2019) | krytyczne KBW: 14 upraw × 4 gleby × 14 okien | frontend, backend |
| Meteorologiczne | Open-Meteo (ERA5 / ERA5-Land, ECMWF IFS, ECMWF ENS) | dzienny opad, ET₀ (FAO-56), Tmax; 10 lat, prognoza 16 dni, prognoza zespołowa | frontend, backend, RL |
| Meteorologiczne | NASA POWER (zapas za Open-Meteo) | dzienny opad, temperatura, wilgotność, wiatr, promieniowanie → ET₀ FAO-56 | backend (frontend przez pośrednika /om) |
| Meteorologiczne | IMGW-PIB (API danych publicznych) | najbliższa stacja: temperatura, odległość, różnica wysokości | backend (data bieżąca) |
| Satelitarne | Copernicus Sentinel-2 L2A Collection 1 (Earth Search STAC) | NDVI, NDMI działki, anomalia względem tego samego okresu w latach poprzednich | backend |
| Ukształtowanie terenu | GUGiK NMT 1 m (WCS), Copernicus DEM GLO-30 (zapas) | spadek, spływ, TWI, zagłębienia | backend |
| Glebowe | ISRIC SoilGrids v2.0 | piasek, ił, węgiel organiczny, woda użyteczna | backend, RL (plan 14 dni) |
| Hydrologiczne | GUGiK BDOT10k (pliki powiatów, opendata.geoportal.gov.pl) | rowy melioracyjne, cieki, kanały, wody powierzchniowe | backend |

Obszar: cała Polska. Okno czasowe: 10 lat wstecz od dnia analizy (sceny Sentinel-2 dla Polski są w katalogu Earth Search od grudnia 2017 r.).

---

## 5. Podział danych: `LIVE` vs `PRE-FETCHED`

Wynik i rekomendacje zawsze liczy aplikacja w chwili zapytania. Wcześniej pobrane są tylko tabele i dane do trenowania modeli.

| Źródło | Tryb | Uwagi |
| --- | --- | --- |
| ULDK, KIEG, ortofotomapa, IUNG WMS | **LIVE** + bufor | Każda nowa działka jest pobierana w chwili wyboru. Granica działki i kategoria gleby są buforowane (7 i 30 dni). |
| Open-Meteo (archiwum i prognoza) | **LIVE** + bufor | Pełne lata archiwum się nie zmieniają, więc przeglądarka i backend je buforują (bieżący rok: 6 h). Prognoza: 30 min (przeglądarka), 1 h (backend). |
| NASA POWER | **LIVE** (zapas) | Tylko gdy Open-Meteo odrzuci zapytania (limit) lub nie odpowie. Raport to zaznacza. |
| Sentinel-2, NMT / Copernicus DEM, SoilGrids, IMGW | **LIVE** + bufor | Sceny Sentinel-2 i model terenu działki są buforowane bez wygasania (się nie zmieniają). |
| BDOT10k | **LIVE** + bufor | Plik powiatu pobierany przy pierwszej analizie w danym powiecie. W `data/cache/bdot10k` jest 11 powiatów pobranych 21–22.11.2026. Skrypt `scripts/prefetch_bdot10k.py` pobiera kolejne. |
| Prognoza zespołowa ECMWF | **LIVE** + bufor (3 h) | Plan 14-dniowy. Dla dat od 2.04.2024 archiwum prognoz (dynamical.org), wcześniej lata analogiczne. |
| Progi IUNG SMSR | **PRE-FETCHED** (21.11.2026) | Tabela `frontend/src/data/iung-progi.json` z `susza.iung.pulawy.pl/progi`. Odświeżenie: `pnpm iung:progi`. |
| Dane treningowe RL | **PRE-FETCHED** (21.11.2026) | Open-Meteo 2016–2026, IUNG, SoilGrids, archiwum ECMWF ENS. Manifesty z datami: `ml/rl_irrigator/data/**/MANIFEST.json`. |
| Dane treningowe HydroFusion-PL | **PRE-FETCHED** (21–22.11.2026) | Próbki Sentinel-2 i SoilGrids w `ml/hydrofusion/data/` (ok. 2,3 MB), opis w [ml/hydrofusion/README.md](ml/hydrofusion/README.md). |

---

## 6. Wykaz bibliotek, komponentów, modeli ML, usług zewnętrznych i narzędzi AI

- **Frontend:** React 18, React Router 6, Vite 5 (+ @vitejs/plugin-react), TypeScript 5, MapLibre GL 4, Vitest 2.
- **Backend:** Python 3.11, FastAPI, Uvicorn, Pydantic v2 (+ pydantic-settings), HTTPX, NumPy, SciPy, Shapely, pyproj, Rasterio (GDAL, affine), LightGBM, Matplotlib, Pillow, Jinja2, WeasyPrint, PyYAML.
- **Trening (grupa `train`, poza aplikacją):** PyTorch (CPU), Stable-Baselines3, sb3-contrib (`MaskablePPO`), Gymnasium, TensorBoard, scikit-learn, Optuna, pandas, PyArrow, xarray, zarr, fsspec, aiohttp, tqdm, rich.
- **Narzędzia deweloperskie (grupa `dev`):** pytest, pytest-asyncio, Ruff, mypy; menedżery pakietów uv i pnpm.
- **Modele ML:**
  - agent nawadniania PPO (`MaskablePPO`), eksportowany do NumPy (`ml/rl_irrigator`, wagi w `artifacts/models/ppo_irrigator_weights.npz`, karta modelu `ppo_irrigator_card.json`);
  - `HydroFusion-PL`: LightGBM (kwantyle 5/50/95%) z przedziałem conformal i obszarem stosowalności AoA, odtwarza NDMI pod chmurami (`artifacts/models/hydrofusion_*`, runtime `src/backend/inference/hydrofusion.py`). Poza obszarem stosowalności model nie jest używany.
- **Usługi zewnętrzne (wszystkie bez konta i bez opłat):**
  - GUGiK: `uldk.gugik.gov.pl`, `mapy.geoportal.gov.pl` (ortofotomapa, NMT WCS), `integracja.gugik.gov.pl` (KIEG), `services.gugik.gov.pl` (wyszukiwarka adresów), `opendata.geoportal.gov.pl` (BDOT10k);
  - IUNG-PIB: `sgeo.iung.pl` (WMS), `susza.iung.pulawy.pl` (progi SMSR);
  - Open-Meteo: `archive-api.open-meteo.com`, `api.open-meteo.com`, `ensemble-api.open-meteo.com`, `previous-runs-api.open-meteo.com`;
  - NASA POWER: `power.larc.nasa.gov`;
  - IMGW-PIB: `danepubliczne.imgw.pl`;
  - Sentinel-2: `earth-search.aws.element84.com` (katalog STAC) i AWS Open Data (pliki COG);
  - Copernicus DEM: `copernicus-dem-30m.s3.amazonaws.com`;
  - ISRIC: `rest.isric.org`;
  - archiwum prognoz ECMWF ENS: `data.dynamical.org` (tylko skrypty treningowe i opcjonalnie plan 14 dni).
- **Narzędzia AI:**
  - przy tworzeniu kodu i dokumentacji: **Claude Code** (Anthropic) – pisanie i przegląd kodu, testy end-to-end, przegląd zgodności z wymaganiami KOWR; **Google Antigravity** – środowisko programistyczne z agentem AI do pisania i refaktoryzacji kodu. Wynik pracy tych narzędzi sprawdzają testy opisane w sekcji 9;
  - w samej aplikacji: modele ML opisane wyżej (PPO, LightGBM). Aplikacja nie używa modeli językowych: wszystkie teksty dla rolnika są generowane regułami z policzonych wartości.

---

## 7. Licencje i warunki korzystania

- **Kod i dokumentacja:** autorskie prawa majątkowe przechodzą na KOWR zgodnie z § 5 Regulaminu.
- **Biblioteki** (wszystkie na licencjach otwartych, pozwalających na użycie komercyjne i przeniesienie praw do rozwiązania):
  - **MIT:** React, React DOM, React Router, Vite, @vitejs/plugin-react, Vitest, @types/react, FastAPI, Pydantic, pydantic-settings, pyproj, LightGBM, PyYAML, GDAL (w kołach Rasterio), Stable-Baselines3, sb3-contrib, Gymnasium, Optuna, zarr, rich, pytest, Ruff, mypy, pnpm;
  - **BSD:** MapLibre GL, NumPy, SciPy, Rasterio, affine, Shapely, Jinja2, WeasyPrint, Uvicorn, HTTPX, PyTorch, scikit-learn, pandas, fsspec;
  - **Apache-2.0:** TypeScript, PyArrow, xarray, TensorBoard, aiohttp, pytest-asyncio; uv (MIT lub Apache-2.0);
  - **inne:** Matplotlib (licencja oparta na PSF, zgodna z BSD), Pillow (MIT-CMU), tqdm (MPL-2.0 i MIT).
- **Dane GUGiK** (ULDK, ortofotomapa, KIEG, NMT, BDOT10k): dane PZGiK udostępniane bez opłat do dowolnych celów (ustawa Prawo geodezyjne i kartograficzne, od 31.07.2020).
- **IUNG-PIB:** publiczna usługa WMS SMSR. Progi pochodzą z Rozporządzenia MRiRW z 11.04.2019.
- **Open-Meteo:** dane CC BY 4.0 (ERA5 Copernicus, ECMWF). Darmowe API jest do użytku niekomercyjnego (limit ok. 10 000 zapytań na dobę). Przy wdrożeniu potrzebna jest własna instancja (kod open source) albo plan komercyjny.
- **NASA POWER:** dane publiczne NASA, bez ograniczeń, z podaniem źródła (NASA Langley Research Center POWER Project).
- **IMGW-PIB:** dane publiczne zgodnie z regulaminem serwisu danepubliczne.imgw.pl; wymagane podanie źródła („Źródłem pochodzenia danych jest Instytut Meteorologii i Gospodarki Wodnej – Państwowy Instytut Badawczy”).
- **Sentinel-2:** dane Copernicus wolne i otwarte (Copernicus Sentinel data [rok]).
- **Copernicus DEM GLO-30:** licencja ESA/Airbus, bezpłatnie z podaniem źródła (© DLR e.V. 2010–2014 i © Airbus Defence and Space GmbH 2014–2018, Copernicus, UE i ESA).
- **ECMWF ENS (Open-Meteo, dynamical.org):** CC BY 4.0.
- **ISRIC SoilGrids:** CC BY 4.0.
- **Zdjęcia w interfejsie:** CC0, domena publiczna i CC BY, z autorami w `frontend/public/img/CREDITS.md`.
- **Narzędzia AI (Claude Code, Google Antigravity):** używane wyłącznie jako narzędzia deweloperskie, na warunkach ich dostawców (Anthropic, Google). Nie są częścią aplikacji i nie są z nią rozpowszechniane.

---

## 8. Znane ograniczenia techniczne i plan skalowania

### Ograniczenia
- **Pogoda:** pochodzi z siatki ok. 9 km (Open-Meteo), a przy zapasie NASA POWER z siatki ok. 50 km, a nie z pomiaru na działce. Lokalna burza może się różnić. Raport zaznacza, które źródło użyto.
- **Gleba:** kategorię I–IV wyznacza 5 punktów na działce (większość głosów). Pojemność wodna to wartość reprezentatywna kategorii, a nie pomiar.
- **Bilans wody:** jednowarstwowy model FAO-56 („wiadro”) ze spływem SCS-CN. Nie uwzględnia wód gruntowych, podsiąku ani drenażu rurkowego.
- **Ewapotranspiracja:** IUNG liczy ją metodą Penmana, a Open-Meteo metodą FAO-56 Penman-Monteith. Wyniki mogą różnić się o kilka procent.
- **Satelita:** NDMI porównujemy z tym samym okresem na tej samej działce w poprzednich latach; w tamtych latach mogła tu rosnąć inna uprawa (płodozmian), a tej informacji nie mamy. Uprawę uwzględniamy przez jej fazę i pokrycie gleby łanem w dniu zdjęcia (krzywa Kc i wysokość rośliny z FAO-56, wzór 76): poza sezonem uprawy NDMI nie wchodzi do oceny, w fazie początkowej i dojrzewania ma mniejszą wagę, w sadach, krzewach, chmielu, truskawkach, warzywach i na łąkach wagę ograniczoną. Parametry tej reguły wymagają weryfikacji z ekspertem; nie mamy wzorcowych wartości NDMI dla każdej uprawy i fazy w Polsce. HydroFusion-PL odtwarza NDMI pod chmurami z R² 0,62, poniżej celu 0,78.
- **Model terenu:** usługa NMT 1 m (WCS GUGiK) nie odpowiadała w testach 22.11.2026 nawet w 120 s. Analiza czeka na nią ok. 4,5 s, potem używa Copernicus DEM 30 m (spadki przybliżone). Pobieranie 1 m trwa dalej w tle; gdy się uda, kolejna analiza działki użyje NMT 1 m.
- **ULDK:** przy przeciążeniu GUGiK przekierowuje zapytania na stronę zastępczą. Pośrednik frontendu ponawia zapytanie do 5 razy, backend buforuje granice działek.
- **Zgody wodnoprawne:** każda zmiana urządzeń wodnych (zastawka, piętrzenie, zbiornik) wymaga zgody Wód Polskich.
- **Parametry upraw** (Kc, p, Ky, terminy) są z FAO-56/33 i wymagają weryfikacji z ekspertem dla warunków Polski.

### Skalowanie
- **Limity i dostępność źródeł:** bufor SQLite (`data/cache`) dla granic działek, gleby, scen Sentinel-2, modelu terenu i pogody; pobieranie równoległe z twardym limitem 25 s; źródło, które nie zdąży, jest w raporcie oznaczone „brak danych” zamiast zatrzymywać analizę; zapasowe źródła (NASA POWER, Copernicus DEM, SoilGrids za IUNG).
- **Ruch:** pogoda jest buforowana na poziomie oczka siatki Open-Meteo, więc jedno oczko obsługuje wiele działek; frontend jest statyczny (dowolny hosting); gotowa analiza (działka, uprawa, data) jest trzymana w pamięci 1 h, więc PDF i „co jeśli” nie liczą jej drugi raz. Przy większym ruchu: Redis lub PostGIS zamiast SQLite, kilka procesów Uvicorn, nocne odświeżanie pogody dla oczek z aktywnymi działkami.
- **Aktualizacja danych:** archiwum pogody i sceny satelitarne starsze niż 7 dni nie zmieniają się; prognoza odświeża się co godzinę; progi IUNG skryptem `pnpm iung:progi`.
- **Koszty:** jeden serwer obsługujący ponad 50 tys. analiz na dobę to szacunkowo ok. 350–500 zł/mies., plus plan komercyjny Open-Meteo albo własna instancja przy wdrożeniu (szacunek zespołu, [docs/SOLUTION_BLUEPRINT.md](docs/SOLUTION_BLUEPRINT.md)). Pozostałe źródła są bezpłatne.

### Bezpieczeństwo i dane osobowe
- **Brak danych osobowych:** aplikacja nie ma kont ani logowania i nie pyta o imię, adres czy PESEL. Wejście to numer działki (publiczne dane EGiB), uprawa i data. Bufor serwera (`data/cache`) zawiera tylko dane publiczne: granice działek, pogodę, zdjęcia satelitarne, model terenu. Przeglądarka zapisuje w `localStorage` tylko bufor pogody. Limit zapytań trzyma adres klienta w pamięci najwyżej 60 s.
- **Walidacja wejścia (Pydantic):** format numeru TERYT sprawdzany przed zapytaniem do ULDK, długość pól tekstowych, współrzędne w granicach Polski, data od 1.01.2016 do dziś, uprawa z zamkniętej listy, najwyżej 12 działek w jednym zapytaniu o strefy.
- **Limit zapytań:** analiza, raport, „co jeśli”, strefy i moduł RL przyjmują najwyżej 120 zapytań na minutę z jednego adresu (`AGRO_RATE_LIMIT_PER_MINUTE`). Nadmiar dostaje HTTP 429 z nagłówkiem `Retry-After`. Chroni to wspólny limit darmowych usług (Open-Meteo). Za pośrednikiem Vite wszystkie zapytania mają adres 127.0.0.1, więc limit działa łącznie; przy wdrożeniu za reverse proxy: `uvicorn --proxy-headers --forwarded-allow-ips=<adres proxy>`.
- **Nagłówki HTTP:** `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, `Permissions-Policy`; raport w wersji HTML ma `Content-Security-Policy` blokujący skrypty i zasoby zewnętrzne.
- **CORS:** tylko adresy z listy `AGRO_CORS_ORIGINS`, metody GET i POST, nagłówek `Content-Type`.
- **Wstrzykiwanie kodu:** bufor SQLite używa wyłącznie zapytań parametryzowanych; szablon raportu (Jinja2) ma automatyczne escapowanie HTML; zapytania do usług zewnętrznych budują parametry przez HTTPX.
- **Pośredniki frontendu** (`/uldk`, `/om`) wysyłają zapytania tylko do stałych adresów GUGiK i Open-Meteo, więc nie działają jako otwarte proxy.
- **Sekrety:** opcjonalny klucz Open-Meteo tylko w pliku `.env` (w `.gitignore`), przechowywany jako `SecretStr`. Repozytorium nie zawiera kluczy.
- **Przy wdrożeniu:** HTTPS na reverse proxy, wyłączenie `/docs`, limit zapytań w proxy lub w Redis (przy kilku procesach Uvicorn).

---

## 9. Sposób weryfikacji wyników i metryki walidacji

1. **Testy jednostkowe frontendu** (`pnpm test`, 38 testów) sprawdzają:
   - parsery ULDK i WKT oraz powierzchnię działki;
   - 14 okien IUNG i zgodność tabeli progów z publikacją IUNG;
   - KBW i skalę stanu wody;
   - zakresy pobierania pogody;
   - bufor analiz i strefy łączone kilku działek.
2. **Testy backendu i modułu RL bez sieci** (`uv run pytest`, 154 testy): fizyka bilansu (domknięcie bilansu masy), oś czasu bez zaglądania w przyszłość, teren, stan wody na osi susza–nadmiar, WaterScore i weta, przyczyny (bez procentów przy braku niedoboru), ranking retencji, waga NDMI zależna od uprawy i jej fazy, limit zapytań i nagłówki bezpieczeństwa, pełna analiza dla każdej z 15 upraw, porady, API kroków 1–5, odmowa dla działki nierolniczej, HydroFusion-PL, środowisko i polityka RL.
3. **Test na prawdziwych usługach** (`pnpm test:live`) przechodzi działkę 040706_5.0050.78/2 od numeru do wyniku.
4. **Test end-to-end backendu** (22.11.2026, prawdziwe usługi): działki z 6 województw, daty 2018–2026, 15 upraw. Analiza trwa 5–16 s; las i zabudowa dostają odmowę; ocena i nawadnianie pokazują ten sam zapas wody. Przykładowe raporty: [reports/](reports/).
5. **Walidacja z IUNG (plan):** werdykt suszy dla działki porównany z wykazem gminnym SMSR z lat 2020–2025.
6. **Walidacja modułu RL:** czasowy i przestrzenny hold-out, opisane w `ml/rl_irrigator/reports/` oraz `artifacts/validation/`.
7. **Walidacja `HydroFusion-PL`** (wilgotność łanu NDMI w dni zachmurzone, 22.11.2026), opis w [ml/hydrofusion/README.md](ml/hydrofusion/README.md):
   - **50 nowych lokalizacji** (12 175 obserwacji Sentinel-2): RMSE 0,133, MAE 0,099, R² 0,62, pokrycie przedziału 90%: 0,89;
   - **lata suszy 2018 i 2023:** RMSE 0,131, R² 0,61, pokrycie 0,91;
   - **porównanie:** model jest lepszy od metody „jak na ostatnim bezchmurnym zdjęciu” (RMSE 0,160) i „jak w poprzednich latach” (RMSE 0,187); bez spełnienia tej reguły model nie byłby wdrożony;
   - **cel ze specyfikacji (R² ≥ 0,78) nie jest osiągnięty;** przyczyny i ograniczenia opisuje raport modelu.
