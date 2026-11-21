# PLAN: moduł RL nawadniania (`ml/rl_irrigator`, branch `feat/RL`)

Moduł obsługuje krok 4 analizy KOWR (nawadnianie, opcjonalne). Codziennie rekomenduje, czy podlać działkę i ile mm. Uczy się rozkładać ograniczony budżet wody na sezon lepiej niż proste reguły. Wynik jest liczony z danych pobieranych na żywo, a działa jako funkcja Pythona i endpoint HTTP.

Status: **zaakceptowany** (21.11.2026), z decyzjami D1–D6 poniżej.

---

## 0. Decyzje wstępne (zaakceptowane 21.11.2026)

| # | Decyzja | Uzasadnienie |
|---|---|---|
| D1 | Projekt powstaje w monorepo jako podprojekt `ml/rl_irrigator/` na branchu `feat/RL`, a nie w osobnym folderze `Desktop\kowr-rl` z własnym `git init`. Commity są lokalne, **bez push**. | Tak zdecydował użytkownik. Blueprint zespołu rezerwuje ten katalog na Tor B. Ścieżka nie ma polskich znaków. |
| D2 | Zależności trafiają do **głównego projektu uv** w katalogu głównym repo: `pyproject.toml`, Python 3.11 z `.python-version`, wspólne `.venv`. Runtime: pyyaml. Grupa `train`: torch CPU z indeksu `pytorch-cpu`, sb3-contrib, tensorboard. Pakiet `kowr_rl` jest dopisany do pakietów hatch, a jego testy do `testpaths`. | Tak zdecydował użytkownik. Jedno środowisko dla całego zespołu i brak rozjazdu wersji. Globalnego Pythona nie ruszam. |
| D3 | Runtime nie potrzebuje torcha. Aktor PPO i statystyki `VecNormalize` są eksportowane do `.npz` (`artifacts/models/ppo_irrigator_weights.npz`), a metryki do `artifacts/validation/rl_eval.json`. Inferencja działa w czystym numpy. Checkpointy SB3 trzymam w `ml/rl_irrigator/models/`. | Tak zakłada blueprint (`docs/SOLUTION_BLUEPRINT.md` §3.4, `docs/IMPLEMENTATION_PLAN.md` Faza 3). Backend zespołu nie ładuje torcha. |
| D4 | Integracja zgodna z zadaniem i blueprintem: `recommend()` i `compare_strategies()` oraz `APIRouter(prefix="/rl")` do podpięcia w `src/backend/main.py` przez `app.include_router(...)`. Odpowiedź ma adapter do kontraktu `IrrigationAdvice` z `src/hydro_core/schemas.py` (karta „czy podlewać w ciągu 7 dni / ile mm / okno” oraz 10-letnia tabela strategii) i wpisy `SourceAuditEntry` z trybem LIVE lub PRE-FETCHED i datą pobrania. Uzasadnienie liczbami, źródła, niepewność i ograniczenia są podane wprost, bez pozornej precyzji (pkt 3.3.5 zadania). | Zespół podpina moduł bez zmian w swoich schematach. Raport spełnia wymagania 3.3 i 4.2 zadania. |
| D5 | Symulator FAO-56 jest częścią modułu, niezależną od Toru A (`hydro_core`), i zostanie ujednolicony, gdy Tor A będzie gotowy. Używam wszystkiego, czego zadanie nie zabrania i co jest darmowe i legalne. Dotyczy to też dodatkowych otwartych źródeł, np. archiwalnych prognoz Open-Meteo (Historical Forecast API, CC BY 4.0) do realistycznej prognozy dla dat historycznych i do kalibracji szumu. | Tak zdecydował użytkownik. Wymóg z pkt 5.2 zadania: dane bezpłatne, legalne i z udokumentowaną licencją. |
| D6 | Kopia `PROMPT_RL_KOWR.md` jest usunięta z repo. Oryginał zostaje w `Desktop\HackNation 2026 - Zadania dla Uczestników\`. | Tak zdecydował użytkownik. |

---

## 1. Kamienie milowe

Każdy kamień kończę uruchomieniem testów, commitem na `feat/RL` i 2–3 zdaniami podsumowania z liczbami.

### M0: Środowisko (~0,5 h)
- **Zakres:** zmiany w głównym `pyproject.toml` (D2). Runtime: pyyaml. Grupa `train`: torch CPU (indeks `download.pytorch.org/whl/cpu` przez `[tool.uv.sources]`), sb3-contrib, tensorboard. Pakiet `ml/rl_irrigator/kowr_rl` jest w buildzie hatch, `ml/rl_irrigator/tests` w `testpaths`. Do tego struktura katalogów i `.gitignore` (dane, logi TB, checkpointy pośrednie).
- **Kryterium ukończenia:** `uv sync --group train` przechodzi na Pythonie 3.11. Smoke test przechodzi: import torch, sb3-contrib i numpy, `torch.from_numpy`, `SubprocVecEnv` z 4 procesami pod `if __name__ == "__main__"` na Windows.
- **Ryzyka:** wheele zależności zespołu (`weasyprint`, `rasterio`) na Windows; torch CPU z `numpy<2.3` z głównego projektu (powinien działać od torch 2.3); przerwy w sieci do PyPI.

### M1: Dane (~1,5 h)
- **Zakres:**
  - `kowr_rl/data.py`: klienci Open-Meteo archive i forecast, IUNG WMS GetFeatureInfo, ULDK (tylko integracja). Cache na dysku, ponawianie z backoffem, pauzy między zapytaniami.
  - `scripts/fetch_training_data.py`: siatka 15 × 9 = 135 punktów (lon 14,2–24,1, lat 49,1–54,8, krok 0,7°). Każdy punkt sprawdzam warstwą IUNG i zostawiam kody 1–4, docelowo 30–50 punktów. Dla nich pobieram pogodę od 2016-01-01 do najnowszej daty archiwum.
  - Zapis: `data/weather/{id}.parquet`, `data/soils/points.json` oraz `data/MANIFEST.json` (URL, data pobrania, zakres dat, liczba dni, braki).
  - Sprawdzam, czy domyślny model archiwum to ERA5-Land, czy mieszanka ERA5. Wybór zapisuję w manifeście.
  - Kontrola jakości: dziury w datach, wartości ujemne, roczne sumy P i ET0 porównane z klimatologią Polski (P ok. 500–750 mm, ET0 ok. 550–700 mm).
- **Limit Open-Meteo:** zapytanie o 10 lat i 4 zmienne liczy się jako ok. 100 „wywołań”, a limity to 600/min i 5000/h. Robię więc pauzę ok. 15 s między punktami, co daje ok. 12 min na 45 punktów. Limit dzielimy z resztą zespołu, jeśli wychodzi z tego samego IP.
- **Kryterium ukończenia:** co najmniej 30 punktów rolnych z co najmniej 4 regionów Polski. Serie są pełne albo braki są opisane w manifeście. Testy parsowania przechodzą na zapisanych odpowiedziach JSON, bez sieci. Dane są w `.gitignore`, a manifest i skrypt w repo.
- **Ryzyka:** odpowiedź 429 z Open-Meteo (wtedy backoff i wznowienie od miejsca przerwania); niedostępny WMS IUNG (cache i ponowienie); za mało punktów rolnych (wtedy zagęszczam siatkę do 0,5°).

### M2: Symulator i strategie bazowe (~2 h)
- **Zakres:**
  - `config/crops.yaml`: 4 uprawy (kukurydza na ziarno, ziemniak, pszenica ozima od wznowienia wegetacji, burak cukrowy). Każda ma Kc ini/mid/end, długości 4 faz, daty siewu i zbioru dla Polski, p i Ky. Przy każdej liczbie zapisuję źródło (FAO-56 tab. 11/12/22, FAO-33, czyli FAO-56 tab. 24) po sprawdzeniu w tekście FAO-56 online. Całość ma flagę `do_weryfikacji_z_mentorem`.
  - `config/soils.yaml`: znaczenie kodów IUNG, TAW reprezentatywne (110/150/185/220 mm) i komunikaty dla kodów, które nie są gruntem ornym.
  - `kowr_rl/simulator.py`: bilans dobowy FAO-56, wektorowy (wiele sezonów naraz × dni). Liczy Ks, ETc, ETa, perkolację i plon względny wg FAO-33. **Odstępstwo od wzoru z promptu:** ETa ograniczam do wody faktycznie dostępnej, zamiast obcinać Dr do TAW. Dzięki temu bilans masy zamyka się dokładnie. Przed siewem liczę rozbieg od 1 marca z Dr = 0 (gleba w pojemności polowej po zimie), żeby stan wody w dniu siewu był realistyczny.
  - `kowr_rl/baselines.py`:
    1. bez nawadniania;
    2. kalendarz: 20 mm co 7 dni w fazie rozwoju i fazie środkowej, do wyczerpania budżetu;
    3. reguła FAO: gdy Dr > RAW, dawka uzupełniająca do pojemności polowej, zaokrąglona do {10, 20, 30} mm, w ramach budżetu;
    4. (opcjonalnie) reguła FAO z prognozą: nie podlewa, gdy prognoza na 3 dni przekracza próg. To uczciwszy rywal, bo pokazuje, ile zysku RL wynika z samego korzystania z prognozy;
    5. wyrocznia: programowanie dynamiczne przy znanej pogodzie (siatka Dr co 2 mm × budżet co 10 mm × dzień), z tą samą funkcją celu co nagroda. To górna granica możliwego zysku.
  - Testy pytest:
    - bilans masy Δzapas = P + I − ETa − DP z tolerancją 1e-9;
    - Ks ∈ [0, 1] i plon ∈ [0, 1];
    - przypadki brzegowe: ET0 = 0, ulewa 200 mm, małe TAW, budżet 0, start z Dr = TAW;
    - wyrocznia osiąga wynik nie gorszy niż każda reguła na tym samym sezonie.
- **Kryterium ukończenia:** testy przechodzą. Mam pomiar wydajności symulatora (kroki/s) i sprawdzenie sensowności strategii bazowych na latach treningowych (np. 2018 bez nawadniania daje wyraźnie niższy plon niż lata mokre).
- **Ryzyka:** niepewne parametry upraw (założenia zapisuję i oznaczam do weryfikacji). TAW z kategorii IUNG nie zależy od głębokości korzeni ani od fazy uprawy. To uproszczenie, opisane w ograniczeniach.

### M3: Środowisko gymnasium (~1,5 h)
- **Zakres:** `kowr_rl/env.py` z klasą `KowrIrrigationEnv`.
  - **Epizod:** losowo wybrany punkt, rok z puli lat (trening/walidacja/test), uprawa, kategoria gleby I–IV i budżet z rozkładu U[0, 250] mm.
  - **Obserwacja (znormalizowana):** postęp sezonu, Kc, Dr/TAW, ΣP i ΣET0 z ostatnich 7 dni, prognoza P na 3 i 7 dni, budżet pozostały/początkowy, TAW, p, Ky. **Dodaję jedną cechę:** pozostały budżet w mm/250, bo sam ułamek nie mówi, ile to milimetrów.
  - **Szum prognozy:** prawdziwa suma × lognormal(0; 0,4). W 15% przypadków deszcz jest „przegapiony” (prognoza × 0–0,2), w 10% pojawia się fałszywy alarm. Parametry są w `config/rl.yaml`. Opcjonalnie skalibruję je na archiwalnych prognozach Open-Meteo (Historical Forecast API, od 2022).
  - **Akcje:** Discrete {0, 10, 20, 30} mm. `action_masks()` blokuje dawki większe niż pozostały budżet.
  - **Nagroda:** wg specyfikacji z promptu. ETc_sezon_szac to klimatologiczna średnia ΣETc dla pary (punkt, uprawa) z lat treningowych. Na koniec epizodu `info` zawiera plon FAO-33, zużytą wodę, perkolację i dni stresu.
  - Testy: `check_env`; przebieg reguły FAO przez środowisko identyczny z bezpośrednim symulatorem (te same Dr, I i plon); maskowanie; powtarzalność przy tym samym ziarnie; obserwacja nie zawiera przyszłości poza zaszumioną prognozą.
- **Kryterium ukończenia:** testy przechodzą. Mam pomiar przepustowości środowiska (1 proces i 16 procesów).

### M4: Trening (~3–4 h, w tle)
- **Zakres:** `kowr_rl/train.py`.
  - Konfiguracja: `MaskablePPO`, `SubprocVecEnv` z 16 środowiskami, `VecNormalize` na obserwacjach, sieć [128, 128], gamma 0,995, gae_lambda 0,95, n_steps 512, batch 4096, lr 3e-4, ent_coef 0,01.
  - Walidacja: `MaskableEvalCallback` na roku 2024 (stały zestaw epizodów walidacyjnych). Logi TensorBoard.
  - **Przebieg 1:** krótki (ok. 300 tys. kroków) jako sprawdzenie sensowności. Oglądam wykresy pojedynczych sezonów (zapas wody, opad, dawki) i szukam miejsc, gdzie agent wykorzystuje luki w symulatorze.
  - **Strojenie:** c_w, c_p, ent_coef, ewentualnie lr i rozmiar sieci. Każda zmiana trafia do `EXPERIMENTS.md` (co, dlaczego, wynik walidacji). **Lat testowych nie używam do strojenia.**
  - **Final:** 2–5 mln kroków, 3 ziarna równolegle (np. po 8 środowisk), wybór najlepszego checkpointu wg walidacji. Na koniec eksport do `.npz` i test zgodności decyzji numpy z torchem.
- **Kryterium ukończenia:** 3 ziarna wytrenowane. Wyniki walidacji są w `EXPERIMENTS.md`. W `models/` leży najlepszy checkpoint i statystyki VecNormalize. Test zgodności `.npz` przechodzi.
- **Ryzyka:** agent nie pokonuje reguły FAO (wtedy rozstrzyga punkt decyzyjny); agent wykorzystuje luki w symulatorze; czas treningu (środowisko w Pythonie daje kilka tysięcy kroków/s na proces, więc szacuję 20–40 min na ziarno przy 5 mln kroków).

### M5: Ewaluacja i punkt decyzyjny (~2 h, równolegle z M4)
- **Zakres:** `kowr_rl/evaluate.py`.
  - **Dane:** lata testowe 2025–2026.
  - **Podział:** uprawa × kategoria gleby × budżet {50, 100, 150, 250} mm. Kategorie gleby liczę w układzie czynnikowym: każdy punkt w każdej z 4 kategorii, żeby wypełnić wszystkie komórki tabeli. Osobno podaję wynik dla prawdziwej kategorii punktu.
  - **Strategie:** bez nawadniania, kalendarz, reguła FAO (oraz FAO z prognozą), RL dla 3 ziaren i wyrocznia.
  - **Metryki:** średni i najgorszy plon względny, zużyta woda, plon na mm (przyrost plonu względem braku nawadniania na mm wody), dni stresu (Ks < 1), perkolacja.
  - **Niepewność:** bootstrap 95% po punktach i latach (2000 powtórzeń), kilka ziaren szumu prognozy. Lata testowe są tylko dwa, więc bootstrap po latach będzie słaby. Napiszę to wprost.
  - `reports/evaluation.md` i wykresy matplotlib: plon i woda z przedziałami, krzywa plon–budżet, 3–4 przykładowe sezony RL vs reguła FAO.
  - **Punkt decyzyjny** (wg promptu): RL jest domyślny tylko wtedy, gdy na teście daje plon co najwyżej 1 pkt proc. niższy niż reguła FAO przy mniejszym zużyciu wody, albo wyższy plon przy tym samym budżecie. W przeciwnym razie domyślna jest reguła FAO, a RL jest oznaczony jako eksperymentalny. Decyzję zapisuję w `config/rl.yaml` (`default_policy`) i opisuję w raporcie.
- **Kryterium ukończenia:** raport generuje się jednym poleceniem, a decyzja jest zapisana.

### M6: Moduł i API (~2 h, równolegle z M4)
- **Zakres:**
  - `kowr_rl/inference.py`, funkcja `recommend(lat, lon, crop, date, budget_mm, budget_used_mm=0)`:
    - **Dane:** pobiera równolegle archiwum od 1 marca roku sezonu do `date`, forecast (gdy `date` jest bliska dzisiejszej, dociąga też ostatnie dni) i kategorię IUNG. Wszystko idzie przez cache. Potem odtwarza bilans do `date`.
    - **Wynik:** plan na 7 dni. Dzień 1 to decyzja polityki, dni 2–7 to plan warunkowy, liczony na prognozie.
    - **Uzasadnienie liczbami:** zapas wody jako % TAW, próg stresu (RAW), faza uprawy, prognoza opadu i pozostały budżet. Do tego źródła z datami pobrania i lista ograniczeń.
    - **Prognoza dla dat historycznych:** archiwalne prognozy z Historical Forecast API (od 2022), jeśli są dostępne. Dla starszych dat używam klimatologii punktu z 10 lat, bez zaglądania w przyszłość. Tryb jest oznaczony w odpowiedzi (`forecast_mode`).
    - Data poza sezonem daje komunikat „poza sezonem” i porównanie historyczne, bez dawek. Kod IUNG inny niż 1–4 daje komunikat „to nie jest grunt orny / brak danych”.
  - `compare_strategies(lat, lon, crop, budget_mm)`: przepuszcza ostatnie 10 sezonów przez wszystkie strategie i zwraca tabelę (plon, woda, perkolacja, plon/mm, dni stresu).
  - `kowr_rl/api.py`: endpointy `/rl/recommend`, `/rl/compare` i `/rl/health` jako `APIRouter`, plus samodzielna aplikacja do testów.
  - `examples/*.json`: suchy lipiec, dzień przed deszczem, listopad poza sezonem, punkt w lesie lub poza Polską.
  - Pomiar czasu na 10 losowych parach punkt × data: start zimny (z siecią) i ciepły (z cache). Wynik zapisuję w README.
- **Kryterium ukończenia:** testy API przechodzą (TestClient na zapisanych odpowiedziach, bez sieci). Czasy są zmierzone i zapisane: poniżej 1 s z cache i poniżej 10 s z pobraniem.

### M7: Dokumentacja (~1 h)
- **Zakres:**
  - `README.md` po polsku: uruchomienie przez uv, architektura w 5 zdaniach, źródła danych z licencjami, **które dane pobrano wcześniej i kiedy** (z `MANIFEST.json`), biblioteki z licencjami, znane ograniczenia, sposób weryfikacji wyników.
  - Znane ograniczenia: model jednowarstwowy, brak podsiąku wód gruntowych, brak spływu powierzchniowego, siatka ERA5-Land ok. 9 km, TAW niezależne od korzeni, parametry upraw do weryfikacji.
  - `SLIDES_NOTES.md`: 3–5 zdań i 2 najważniejsze wykresy.
  - Domknięcie `EXPERIMENTS.md` i krótka instrukcja dla zespołu, jak podpiąć router w `src/backend/main.py`.
- **Kryterium ukończenia:** wszystkie testy przechodzą, a moduł uruchamia się z czystego klonu po pobraniu danych skryptem.

---

## 2. Harmonogram orientacyjny (~13 h + bufor)

| Godz. | Praca na pierwszym planie | W tle |
|---|---|---|
| 0:00–0:30 | M0 | |
| 0:30–2:00 | M1 | pobieranie danych |
| 2:00–4:00 | M2 | |
| 4:00–5:30 | M3 | |
| 5:30–6:00 | M4: przebieg sprawdzający i oględziny sezonów | trening |
| 6:00–8:30 | M6: moduł i API | strojenie i finalne ziarna |
| 8:30–10:30 | M5: ewaluacja i decyzja | |
| 10:30–11:30 | M7 | |
| 11:30–14:00 | bufor | |

---

## 3. Struktura

```
pyproject.toml            (główny projekt uv; zależności modułu dopisane zgodnie z D2)
artifacts/models/ppo_irrigator_weights.npz, artifacts/validation/rl_eval.json   (D3)
ml/rl_irrigator/
  PLAN.md, EXPERIMENTS.md, README.md, SLIDES_NOTES.md
  config/      crops.yaml, soils.yaml, rl.yaml
  data/        weather/, soils/, cache/, MANIFEST.json   (dane w .gitignore, manifest w repo)
  kowr_rl/     data.py, simulator.py, baselines.py, env.py, train.py, evaluate.py, inference.py, api.py
  scripts/     fetch_training_data.py, benchmark_latency.py
  tests/
  models/      najlepszy checkpoint + statystyki VecNormalize + policy.npz
  reports/     evaluation.md, wykresy
  examples/    przykładowe odpowiedzi JSON
```

---

## 4. Rejestr decyzji podjętych w trakcie

Uzupełniam na bieżąco: data, decyzja, powód.

- 2026-11-21: decyzje D1–D6 zaakceptowane. D2 zmieniona: zamiast osobnego projektu uv zależności trafiają do głównego `pyproject.toml`.
