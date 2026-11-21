# Rusałka: asystent wody dla działki rolnej

> **Zadanie:** HackNation 2026, KOWR: *Zatrzymaj wodę, chroń plony – Inteligentne wsparcie gospodarowania wodą w rolnictwie*
> **Repozytorium:** prywatne repozytorium konkursowe. Kod jest pisany od zera zgodnie z § 5 Regulaminu KOWR.
> **Dokumentacja:**
> - [docs/SOLUTION_BLUEPRINT.md](docs/SOLUTION_BLUEPRINT.md): koncepcja i metoda;
> - [docs/IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md): plan i stan prac;
> - [frontend/README.md](frontend/README.md): aplikacja webowa.

Rusałka pokazuje stan wody na dowolnej działce rolnej w Polsce, dla dowolnej daty z ostatnich 10 lat. Wynik zawiera:
- ocenę suszy według progów IUNG-PIB;
- przyczyny niedoboru wody;
- działania retencyjne z symulacją;
- poradę o nawadnianiu.

Wzorem jest System Monitoringu Suszy Rolniczej IUNG-PIB, liczony na poziomie jednej działki (ustalenie z mentorami KOWR, 21.11.2026).

---

## 1. Instrukcja uruchomienia rozwiązania

### Wymagania
- Node.js 20+ i [`pnpm`](https://pnpm.io/) (frontend).
- Python 3.11 i [`uv`](https://docs.astral.sh/uv/) (backend i moduły ML).

### Aplikacja webowa (frontend)

```bash
cd frontend
pnpm install
pnpm build
pnpm preview      # http://localhost:4173
```

Aplikacja działa w przeglądarkach Chrome, Edge, Firefox i Safari oraz na telefonie. Użytkownik nic nie instaluje. Szczegóły i tryb deweloperski: [frontend/README.md](frontend/README.md).

### Backend (FastAPI)

```bash
uv sync
uv run uvicorn backend.main:app --host 0.0.0.0 --port 8000
```

Adres backendu dla frontendu ustawia `VITE_API_URL` w `frontend/.env`. Bez backendu frontend liczy ocenę IUNG w przeglądarce, a kroki serwera są ukryte.

### Moduł nawadniania RL

Moduł leży na gałęzi `feat/RL` w `ml/rl_irrigator`. Udostępnia `POST /rl/recommend` i `POST /rl/compare`. Opis decyzji: `docs/RL_IRRIGATION_DECISION_MODEL.md` na tej gałęzi.

---

## 2. Opis podstawowych funkcjonalności

1. **Wybór działek na mapie.**
   - Ortofotomapa GUGiK z granicami działek ewidencyjnych.
   - Działkę dodajesz kliknięciem albo numerem TERYT. Możesz wybrać kilka i dla każdej ustawić uprawę.
   - Dla działki nierolniczej (las, woda, zabudowa) serwis pokazuje komunikat zamiast analizy.
2. **Ocena sytuacji wodnej** dla wybranej daty:
   - stan wody słownie i na skali graficznej (od suszy przez optimum do nadmiaru wody);
   - ryzyko i pewność danych;
   - wykres stanu dzień po dniu z prognozą na kolejne dni;
   - 14 okien klimatycznego bilansu wodnego (KBW) z progami IUNG dla uprawy i kategorii gleby;
   - ten sam dzień albo miesiąc w 10 latach.
3. **Przyczyny niedoboru wody:** opad i parowanie na tle średniej z 10 lat, kategoria gleby IUNG. Backend dodaje udział procentowy przyczyn, spływ z modelu terenu i fazę uprawy.
4. **Działania i symulacja „co jeśli”.**
   - Lista działań jest posortowana według zysku wody na tej działce.
   - Działania na urządzeniach wodnych mają oznaczenie „Wymaga zgody Wód Polskich”.
   - Wybrane działania przelicza symulacja na tych samych danych.
5. **Nawadnianie:**
   - w sezonie: porada z prognozy opadu („poczekaj, za 5 dni prognozowany opad”);
   - z modułem RL: dawka na dziś, plan na 7 dni i porównanie strategii z 10 sezonów.
6. **Raport:** tabela źródeł z czasem pobrania. Przycisk „Pobierz raport PDF” drukuje stronę, a z backendem pobiera PDF z serwera.

---

## 3. Skrócony opis architektury rozwiązania

```text
Przeglądarka (frontend/, React + MapLibre)
  ├─ ULDK, KIEG, ortofotomapa GUGiK, IUNG WMS, Open-Meteo (przez pośrednika /om)
  ├─ src/analysis/local.ts: KBW w oknach IUNG, stan wody, symulacja (tymczasowo w przeglądarce)
  └─ src/api/: podpięcia do backendu (VITE_API_URL)
Backend (src/backend, FastAPI)        → POST /api/analyze, GET /api/report.pdf
  └─ src/hydro_core: wzór stanu wody: bilans FAO-56 + Sentinel-2/ML + gleba + teren, weto IUNG
Moduł RL (ml/rl_irrigator, feat/RL)    → POST /rl/recommend, POST /rl/compare
```

- Frontend działa bez backendu i nie używa makiet. Każda liczba pochodzi z obliczenia na prawdziwych danych.
- Gdy backend odpowiada, jego wynik zastępuje obliczenie przeglądarki. Kontrakt jest w `frontend/src/api/contract.ts`.

---

## 4. Wykorzystane źródła i zakresy danych

| Kategoria | Źródło | Zakres | Gdzie |
| --- | --- | --- | --- |
| Przestrzenne | GUGiK ULDK | granica działki, gmina, obręb | frontend |
| Przestrzenne | GUGiK ortofotomapa (WMTS i WMS), KIEG (WMS) | podkład mapy, granice i numery działek | frontend |
| Glebowo-rolnicze | IUNG-PIB WMS `Susza:katgl_landuse2024` | kategoria gleby I–IV, użytkowanie | frontend, RL |
| Glebowo-rolnicze | IUNG-PIB progi SMSR (Rozp. MRiRW z 11.04.2019) | krytyczne KBW: 14 upraw × 4 gleby × 14 okien | frontend |
| Meteorologiczne | Open-Meteo: archiwum ERA5 i prognoza ECMWF | dzienny opad i ET₀ (FAO-56), 10 lat i 15 dni prognozy | frontend, RL |
| Satelitarne | Sentinel-2 L2A (Earth Search STAC) | NDVI, NDMI | backend (w przygotowaniu) |
| Ukształtowanie terenu | GUGiK NMT 1 m (WCS) | spadek, spływ, TWI | backend (w przygotowaniu) |
| Glebowe | ISRIC SoilGrids v2.0 | piasek, ił, węgiel organiczny | backend (w przygotowaniu) |
| Hydrologiczne | BDOT10k (WMS), EDO (WMS) | cieki, rowy, tło suszowe | backend (w przygotowaniu) |

Obszar: cała Polska. Okno czasowe: 10 lat wstecz od dnia analizy.

---

## 5. Podział danych: `LIVE` vs `PRE-FETCHED`

| Źródło | Tryb | Uwagi |
| --- | --- | --- |
| ULDK, KIEG, ortofotomapa, IUNG WMS | **LIVE** | Każda analiza pyta usługę w chwili wyboru działki. |
| Open-Meteo | **LIVE** + cache przeglądarki | Archiwum starsze niż 7 dni się nie zmienia, więc przeglądarka trzyma je w `localStorage`. |
| Progi IUNG SMSR | **PRE-FETCHED** (21.11.2026) | Tabela `frontend/src/data/iung-progi.json` z `susza.iung.pulawy.pl/progi`. Odświeżenie: `pnpm iung:progi`. |
| Dane treningowe RL | **PRE-FETCHED** | Wersjonowany zbiór w `ml/rl_irrigator/data` (gałąź `feat/RL`). |

---

## 6. Wykaz bibliotek, komponentów, modeli ML, usług zewnętrznych i narzędzi AI

- **Frontend:** React 18, React Router, Vite 5, TypeScript, MapLibre GL 4, Vitest.
- **Backend:** Python 3.11, FastAPI, Pydantic v2, HTTPX, NumPy, Rasterio, WeasyPrint.
- **Modele ML:**
  - agent nawadniania PPO (MaskablePPO), eksportowany do NumPy (`ml/rl_irrigator`);
  - rekonstrukcja NDMI i atrybucja przyczyn (w przygotowaniu).
- **Usługi zewnętrzne:**
  - `uldk.gugik.gov.pl`
  - `mapy.geoportal.gov.pl`
  - `integracja.gugik.gov.pl`
  - `sgeo.iung.pl`
  - `susza.iung.pulawy.pl`
  - `archive-api.open-meteo.com`
  - `api.open-meteo.com`
- **Narzędzia AI:** asystenci programistyczni przy projektowaniu i pisaniu kodu (do uzupełnienia dokładnymi nazwami przy oddaniu). Kalkulator rolnika nie używa modeli językowych.

---

## 7. Licencje i warunki korzystania

- **Kod i dokumentacja:** autorskie prawa majątkowe przechodzą na KOWR zgodnie z § 5 Regulaminu.
- **Biblioteki:** licencje permisywne (MIT, BSD, Apache-2.0).
- **Dane GUGiK** (ULDK, ortofotomapa, KIEG, NMT): otwarte dane PZGiK.
- **IUNG-PIB:** publiczna usługa WMS. Progi pochodzą z Rozporządzenia MRiRW z 11.04.2019.
- **Open-Meteo:** CC BY 4.0 (ERA5 Copernicus). Przy skali produkcyjnej potrzebna jest własna instancja albo licencja komercyjna.
- **Sentinel-2 i EDO:** licencja Copernicus i JRC (CC BY 4.0).
- **ISRIC SoilGrids:** CC BY 4.0.
- **Zdjęcia w interfejsie:** CC0, domena publiczna i CC BY, z autorami w `frontend/public/img/CREDITS.md`.

---

## 8. Znane ograniczenia techniczne i plan skalowania

### Ograniczenia
- **Pogoda:** pochodzi z siatki ok. 9 km, a nie z pomiaru na działce. Lokalna burza może się różnić.
- **Gleba:** kategorię I–IV wyznacza jeden punkt w środku działki.
- **Ewapotranspiracja:** IUNG liczy ją metodą Penmana, a Open-Meteo metodą FAO-56 Penman-Monteith. Wyniki mogą różnić się o kilka procent.
- **Stan wody w przeglądarce:** jest tymczasowy, bo wynika tylko z KBW i progu IUNG. Pełny wzór liczy backend.
- **Model terenu:** NMT 1 m przez WCS GUGiK odpowiadał w testach po ponad 20 s. Backend musi go buforować.
- **Zgody wodnoprawne:** każda zmiana urządzeń wodnych (zastawka, piętrzenie, zbiornik) wymaga zgody Wód Polskich.

### Skalowanie
- **Cache pogody:** po stronie serwera, na poziomie oczka siatki Open-Meteo. Jedno oczko obsługuje wiele działek.
- **Statyczny frontend:** dowolny hosting statyczny, obliczenia lekkie (poniżej 10 ms na działkę).
- **Wyniki backendu:** cache par (działka, data) w bazie.

---

## 9. Sposób weryfikacji wyników i metryki walidacji

1. **Testy jednostkowe frontendu** (`pnpm test`) sprawdzają:
   - parsery ULDK i WKT;
   - powierzchnię działki;
   - 14 okien IUNG;
   - zgodność tabeli progów z publikacją IUNG;
   - KBW i skalę stanu wody;
   - zakresy pobierania pogody.
2. **Test na prawdziwych usługach** (`pnpm test:live`) przechodzi działkę 040706_5.0050.78/2 od numeru do wyniku. Pomiar z 21.11.2026: ok. 0,8 s.
3. **Czasy w przeglądarce** (pomiar z 21.11.2026):
   - otwarcie analizy z linku: ok. 1,1 s do wyniku;
   - przejście z mapy do analizy: ok. 90 ms przy danych pobranych wcześniej.
4. **Walidacja z IUNG (plan):** werdykt suszy dla działki porównany z wykazem gminnym SMSR z lat 2020–2025.
5. **Walidacja modułu RL:** czasowy i przestrzenny hold-out, opisane w `ml/rl_irrigator/reports/` na gałęzi `feat/RL`.
