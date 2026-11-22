<div align="center">

# Rusałka

### Asystent wody dla działki rolnej

**Zatrzymaj wodę tam, gdzie spadła.** Numer działki, uprawa i data wystarczą, żeby w kilkanaście sekund dostać ocenę wody na polu, przyczyny niedoboru, działania retencyjne i plan nawadniania, policzone z otwartych danych publicznych.

`HackNation 2026` · `KOWR` · `Python 3.11` · `FastAPI` · `React 18` · `MapLibre` · `FAO-56` · `IUNG-PIB SMSR` · `100% danych otwartych` · `bez logowania` · `5–16 s na analizę`

**[Otwórz Rusałkę → licho-cachyos-pc.tail886ef4.ts.net](https://licho-cachyos-pc.tail886ef4.ts.net/)**

</div>

---

## Karta zgłoszenia

Wymagania formalne z pkt 4.1 opisu zadania.

| Element | Wartość |
| --- | --- |
| **Nazwa zespołu** | SolvronGen Racing In Space |
| **Tytuł rozwiązania** | Rusałka – asystent wody dla działki rolnej |
| **Zadanie** | HackNation 2026, KOWR: *Zatrzymaj wodę, chroń plony – Inteligentne wsparcie gospodarowania wodą w rolnictwie* |
| **Działający prototyp** | [https://licho-cachyos-pc.tail886ef4.ts.net/](https://licho-cachyos-pc.tail886ef4.ts.net/) (instrukcja uruchomienia lokalnego: [sekcja 1](#1-instrukcja-uruchomienia-rozwiązania)) |
| **Repozytorium** | [github.com/Marcelele-0/agroretencja-kowr](https://github.com/Marcelele-0/agroretencja-kowr), prywatne do zakończenia Wydarzenia (pkt 4.3) |
| **Dokumentacja** | ten plik (9 sekcji z pkt 4.2) i katalog [docs/](docs/) |
| **Prezentacja (PDF, do 10 slajdów)** | załączona w formularzu zgłoszeniowym na Platformie konkursowej |
| **Film (do 3 min)** | załączony w formularzu zgłoszeniowym; kod montażu w [promo/](promo/) |
| **Raporty wygenerowane przez prototyp** | [reports/](reports/): 3 raporty PDF |
| **Materiał dla jury (2 strony A4)** | [docs/materialy/Rusalka-material-dla-jury.pdf](docs/materialy/Rusalka-material-dla-jury.pdf) |

---

## Sprawdź w 3 minuty

1. Otwórz **[licho-cachyos-pc.tail886ef4.ts.net](https://licho-cachyos-pc.tail886ef4.ts.net/)**. Serwis nie wymaga logowania ani instalacji i działa też na telefonie.
2. Kliknij jedną z gotowych działek poniżej albo wpisz własny numer działki ewidencyjnej.
3. Poczekaj na wynik (zwykle 5–16 s) i pobierz raport PDF przyciskiem „Pobierz raport PDF”.

| # | Działka (TERYT) | Uprawa | Data | Region | Co warto zobaczyć | Analiza | Raport |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 01 | `040706_5.0050.78/2` | Kukurydza na ziarno | 10.07.2025 | kujawsko-pomorskie | Środek lata: stan wody, werdykt wg progów IUNG-PIB, dawka nawadniania i plan na 14 dni | [otwórz](https://licho-cachyos-pc.tail886ef4.ts.net/analiza?dzialka=040706_5.0050.78%2F2&uprawa=kukurydza_ziarno&data=2025-07-10) | [PDF](reports/raport_040706_5.0050.78-2_kukurydza_2025-07-10.pdf) |
| 02 | `040706_5.0050.78/2` | Truskawki | 1.06.2024 | kujawsko-pomorskie | Ta sama działka, inna uprawa i inny rok: zmieniają się progi suszy, przyczyny i porada | [otwórz](https://licho-cachyos-pc.tail886ef4.ts.net/analiza?dzialka=040706_5.0050.78%2F2&uprawa=truskawki&data=2024-06-01) | [PDF](reports/raport_040706_5.0050.78-2_truskawki_2024-06-01.pdf) |
| 03 | `141403_2.0005.145` | Kukurydza na ziarno | 15.07.2024 | mazowieckie | Inny region i inna gleba: inny udział przyczyn i inna kolejność działań niż w 01 | [otwórz](https://licho-cachyos-pc.tail886ef4.ts.net/analiza?dzialka=141403_2.0005.145&uprawa=kukurydza_ziarno&data=2024-07-15) | [PDF](reports/raport_141403_2.0005.145_kukurydza_2024-07-15.pdf) |

> [!NOTE]
> **Wynik jest liczony na żywo.** Rusałka nie pokazuje przygotowanych wcześniej przykładów. Przy każdej analizie pobiera dane z kilku publicznych serwisów (GUGiK, IUNG-PIB, Open-Meteo, Sentinel-2 i in.). Przy słabym łączu internetowym albo przeciążeniu tych serwisów analiza i odświeżanie mapy mogą trwać dłużej niż zwykłe 5–16 s. Pierwsze otwarcie serwisu i pierwsza analiza w nowym powiecie też trwają kilka sekund dłużej, bo dane trafiają wtedy do bufora.

**Własna działka.** Wpisz numer TERYT (np. `141201_1.0001.6509`), nazwę obrębu z numerem działki albo kliknij działkę na ortofotomapie. Dawkę nawadniania najlepiej sprawdzić dla daty w sezonie (21.III–30.IX). Las, wody i zabudowa dostają komunikat zamiast analizy.

---

## Opis rozwiązania

### Problem

Polska ma jedne z najmniejszych zasobów wody w Europie, a susze rolnicze zdarzają się coraz częściej. Dane, które pozwalają ocenić wodę na polu (satelitarne, meteorologiczne, glebowe, hydrologiczne, przestrzenne), są otwarte, ale rozproszone po kilkunastu serwisach i wymagają wiedzy z GIS, teledetekcji i hydrologii. Rolnik nie ma prostej odpowiedzi na pytania: *czy moje pole ma za mało wody, dlaczego i co mogę z tym zrobić?*

### Rozwiązanie

Rusałka działa jak System Monitoringu Suszy Rolniczej IUNG-PIB, ale dla jednej działki ewidencyjnej, i dodaje do oceny konkretne rekomendacje (ustalenie z mentorami KOWR, 21.11.2026). Rolnik wybiera działkę, uprawę i datę. Serwis prowadzi analizę w kolejności z opisu zadania i z materiału „Podstawy naukowe”:

```text
 ① Ocena sytuacji wodnej  →  ② Przyczyny niedoboru  →  ③ Retencja i agrotechnika  →  ④ Nawadnianie  →  ⑤ Raport PDF
    stan wody, susza IUNG,      klimat / gleba /          działania posortowane        dawka na dziś,      źródła, daty,
    10 lat historii             spływ / uprawa            wg zysku wody na działce     plan 14 dni         ograniczenia
```

Każdy krok korzysta z wyniku poprzedniego. Rekomendacje powstają dopiero po ocenie warunków tej działki.

### Główne funkcjonalności

- **Mapa całej Polski** z ortofotomapą i granicami działek. Działkę wybiera się kliknięciem, numerem TERYT albo nazwą obrębu z numerem. Można wybrać kilka działek i każdej przypisać inną uprawę.
- **Ocena wody** dla dowolnej daty od 1.01.2016 do dziś: stan wody na skali od suszy skrajnej do zalania, werdykt suszy wg progów IUNG-PIB, ryzyko, pewność danych i ten sam dzień w 10 poprzednich latach.
- **Przyczyny niedoboru**: udział klimatu, gleby, spływu z terenu i uprawy w procentach.
- **Działania retencyjne i agrotechniczne** posortowane według zysku wody na tej działce, z symulatorem „co jeśli”.
- **Nawadnianie**: dawka na dziś i plan na 14 dni z prognozy zespołowej ECMWF.
- **Raport PDF** ze źródłami danych, datami pobrania, niepewnością i ograniczeniami.

### Sposób wykorzystania danych

W chwili zapytania aplikacja pobiera dane z **8 grup otwartych źródeł** (pobieranie równoległe, twardy limit 25 s) i łączy je w jednym bilansie wodnym FAO-56:

| Kategoria KOWR | Co bierzemy | Do czego służy |
| --- | --- | --- |
| Przestrzenne | GUGiK ULDK, ortofotomapa, KIEG, NMT 1 m, Copernicus DEM | granica działki, spadek terenu, spływ, zagłębienia |
| Meteorologiczne | Open-Meteo (ERA5, ECMWF), NASA POWER, IMGW-PIB | opad i parowanie z 10 lat, prognoza na 14 dni |
| Glebowe | IUNG-PIB (kategoria gleby, progi SMSR), ISRIC SoilGrids | pojemność wodna gleby, progi suszy dla uprawy |
| Satelitarne | Copernicus Sentinel-2 L2A | wilgotność (NDMI) i kondycja (NDVI) łanu |
| Hydrologiczne | GUGiK BDOT10k | rowy melioracyjne, cieki, kanały, zbiorniki przy działce |

Liczby i rekomendacje liczy fizyka (FAO-56, SCS-CN, progi IUNG-PIB). Dwa modele uczenia maszynowego tylko ją uzupełniają: jeden odtwarza NDMI pod chmurami, drugi dobiera dawkę wody. Teksty dla rolnika powstają z reguł na podstawie policzonych wartości, bez modelu językowego.

### Korzyści dla rolnika

- **Konkretna decyzja dla tej działki**, a nie mapa dla powiatu: ile wody jest w glebie, czy to susza wg IUNG-PIB, co zrobić i ile wody to da w milimetrach.
- **Wiarygodność:** wynik da się sprawdzić. Raport podaje źródła, daty, użyte zapasy i niepewność. Brak danych jest napisany wprost.
- **Oszczędność wody:** agent nawadniania zużył na nowych lokalizacjach o 45% mniej wody niż podlewanie wg kalendarza, przy wyższym plonie (sekcja 9).
- **Retencja przed nawadnianiem:** działania „zatrzymaj wodę, gdzie spadła” są przed nawadnianiem. Urządzenia wodne mają oznaczenie „Wymaga zgody Wód Polskich”.
- **Zero barier:** trzy wybory (działka, uprawa, data), bez konta, bez instalacji, bez żargonu GIS, na komputerze i telefonie.

---

## Zgodność z wymaganiami KOWR

| Wymaganie (opis zadania) | Jak je spełniamy | Gdzie |
| --- | --- | --- |
| 3.2 Aplikacja webowa, Windows, Chrome / Firefox / Edge / Safari / Opera, bez instalacji | statyczny frontend React + backend na serwerze; użytkownik potrzebuje tylko przeglądarki; układ działa też na telefonie | [sekcja 1](#1-instrukcja-uruchomienia-rozwiązania) |
| 3.3 pkt 1–2 Działka z numeru lub z mapy | numer TERYT, obręb z numerem albo klik na ortofotomapie | [sekcja 2](#2-opis-podstawowych-funkcjonalności) |
| 3.3 pkt 3 Wybór uprawy | 15 grup upraw: 14 grup SMSR IUNG-PIB oraz łąki i pastwiska | [Uprawy](#obsługiwane-uprawy) |
| 3.3 pkt 4 Ocena bieżąca i historyczna z danych pobranych na żywo, 10 lat | dane pobierane w chwili zapytania; ten sam dzień w 10 latach; brak gotowych rekomendacji | [sekcja 5](#5-dane-live-vs-pre-fetched) |
| 3.3 pkt 5 Raport: stan, przyczyny, rekomendacje, data i źródła, ograniczenia, brak danych wprost | raport PDF z tabelą źródeł (tryb, czas pobrania), niepewnością i ograniczeniami | [sekcja 2](#2-opis-podstawowych-funkcjonalności), [reports/](reports/) |
| 3.3 pkt 6 Zapis lub druk raportu | „Pobierz raport PDF”; bez serwera druk strony | [sekcja 2](#2-opis-podstawowych-funkcjonalności) |
| 3.3 Pięć kategorii danych | meteorologiczne, satelitarne, glebowe, hydrologiczne, przestrzenne | [sekcja 4](#4-wykorzystane-źródła-i-zakresy-danych) |
| 3.2 Kolejność: ocena → retencja i agrotechnika → nawadnianie | 5 kroków w tej kolejności; każdy korzysta z wyniku poprzedniego | [Opis rozwiązania](#opis-rozwiązania) |
| 3.2 Interpretacja satelity zależna od uprawy | waga NDMI zależy od fazy uprawy i pokrycia gleby łanem (FAO-56, wzór 76) | [sekcja 2](#2-opis-podstawowych-funkcjonalności) |
| 4.2 Dokumentacja: 9 punktów | sekcje 1–9 tego pliku, w tej samej kolejności | [Spis treści](#spis-treści) |
| 5.1 Zakres i sposób użycia AI | 2 modele ML w aplikacji; narzędzia AI tylko przy tworzeniu kodu; brak modeli językowych w aplikacji | [sekcja 6](#6-wykaz-bibliotek-komponentów-modeli-usług-zewnętrznych-i-narzędzi-ai) |
| 5.2 Dane bezpłatne i legalne | wszystkie źródła bez konta i bez opłat | [sekcja 7](#7-licencje-i-warunki-korzystania) |
| 5.3 Źródła i licencje: dane, biblioteki, komponenty, modele, usługi, API, narzędzia AI | pełny wykaz z licencjami | [sekcje 6 i 7](#6-wykaz-bibliotek-komponentów-modeli-usług-zewnętrznych-i-narzędzi-ai) |
| 5.4 Bez płatnej licencji, sprzętu, GIS, programowania, instalacji; koszty płatnych usług | jedyna potencjalnie płatna usługa (Open-Meteo przy wdrożeniu) opisana z kosztem i alternatywą | [Dostępność i koszty](#dostępność-dla-użytkownika-i-koszty-pkt-54) |
| 5.5 Dowolna działka w Polsce; limity, aktualizacja, koszty przy skalowaniu | cała Polska; bufory, zapasowe źródła, limit zapytań, plan i koszt wdrożenia | [sekcja 8](#skalowanie-pkt-55) |
| 5.6 Bezpieczeństwo, brak danych osobowych | brak kont i danych osobowych, walidacja wejścia, limit zapytań, nagłówki HTTP | [sekcja 8](#bezpieczeństwo-i-dane-osobowe-pkt-56) |
| 6 Dowolna działka i data z 10 lat | dowolny dzień od 1.01.2016 do dziś, cała Polska | [Sprawdź w 3 minuty](#sprawdź-w-3-minuty) |
| 7 Rekomendacja w czasie ≤ 30 s | zwykle 5–16 s; twardy limit 25 s na pobranie danych | [sekcja 9](#9-sposób-weryfikacji-wyników-i-metryki-walidacji) |

---

## Spis treści

- [Karta zgłoszenia](#karta-zgłoszenia)
- [Sprawdź w 3 minuty](#sprawdź-w-3-minuty)
- [Opis rozwiązania](#opis-rozwiązania)
- [Zgodność z wymaganiami KOWR](#zgodność-z-wymaganiami-kowr)
- [1. Instrukcja uruchomienia rozwiązania](#1-instrukcja-uruchomienia-rozwiązania)
- [2. Opis podstawowych funkcjonalności](#2-opis-podstawowych-funkcjonalności)
- [3. Skrócony opis architektury rozwiązania](#3-skrócony-opis-architektury-rozwiązania)
- [4. Wykorzystane źródła i zakresy danych](#4-wykorzystane-źródła-i-zakresy-danych)
- [5. Dane LIVE vs PRE-FETCHED](#5-dane-live-vs-pre-fetched)
- [6. Wykaz bibliotek, komponentów, modeli, usług zewnętrznych i narzędzi AI](#6-wykaz-bibliotek-komponentów-modeli-usług-zewnętrznych-i-narzędzi-ai)
- [7. Licencje i warunki korzystania](#7-licencje-i-warunki-korzystania)
- [8. Znane ograniczenia, skalowanie i bezpieczeństwo](#8-znane-ograniczenia-skalowanie-i-bezpieczeństwo)
- [9. Sposób weryfikacji wyników i metryki walidacji](#9-sposób-weryfikacji-wyników-i-metryki-walidacji)
- [Podstawy naukowe](#podstawy-naukowe)
- [Struktura repozytorium](#struktura-repozytorium)
- [Dalsza dokumentacja](#dalsza-dokumentacja)
- [Zespół](#zespół)

---

## 1. Instrukcja uruchomienia rozwiązania

### Wariant A: wersja online (zalecana dla jury)

Otwórz **[https://licho-cachyos-pc.tail886ef4.ts.net/](https://licho-cachyos-pc.tail886ef4.ts.net/)** w dowolnej przeglądarce (Chrome, Edge, Firefox, Safari, Opera) na komputerze albo telefonie. Nic nie trzeba instalować ani konfigurować.

### Wariant B: uruchomienie lokalne

#### Wymagania

| Składnik | Wersja | Uwagi |
| --- | --- | --- |
| System | Windows 10/11, Linux lub macOS | rolnik potrzebuje tylko przeglądarki |
| Python | 3.11 | plik [.python-version](.python-version) |
| [uv](https://docs.astral.sh/uv/) | aktualna | instaluje zależności Pythona z [uv.lock](uv.lock) |
| Node.js | 20+ | frontend |
| [pnpm](https://pnpm.io/) | 9+ | jeśli go nie ma: `corepack enable` (Node 20+ ma corepack) albo `npm install -g pnpm` |
| Internet | wymagany | aplikacja pobiera dane z serwisów publicznych na żywo |

#### Krok 1: backend (FastAPI, port 8000)

```bash
uv sync
uv run uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

Sprawdzenie: [http://127.0.0.1:8000/api/health](http://127.0.0.1:8000/api/health) zwraca `"status": "ok"` i listę 15 upraw. Dokumentacja API (Swagger): [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs).

#### Krok 2: aplikacja webowa (w drugim terminalu)

```bash
cd frontend
pnpm install
pnpm build
pnpm preview
```

Aplikacja działa pod adresem [http://localhost:4173](http://localhost:4173).

- Frontend łączy się z backendem przez pośrednika Vite (`/api`, `/rl` → `127.0.0.1:8000`), więc plik `frontend/.env` nie jest potrzebny.
- Inny adres backendu ustawia zmienna `VITE_API_URL` (wzór w [frontend/.env.example](frontend/.env.example)).
- `VITE_API_URL=` (puste) wyłącza backend. Zostaje wtedy uproszczona ocena IUNG liczona w przeglądarce.
- Tryb deweloperski: `pnpm dev` ([http://localhost:5173](http://localhost:5173)).

#### Konfiguracja (opcjonalna)

Wszystkie ustawienia mają wartości domyślne. Żeby je zmienić, skopiuj [.env.example](.env.example) do `.env`. Najważniejsze zmienne:

| Zmienna | Domyślnie | Znaczenie |
| --- | --- | --- |
| `AGRO_ANALYSIS_DEADLINE_S` | `25` | łączny limit czasu pobierania danych dla jednej działki [s] |
| `AGRO_ANALYSIS_CACHE_TTL_S` | `3600` | jak długo gotowa analiza jest w pamięci (PDF i „co jeśli” bez ponownego liczenia) [s] |
| `AGRO_CORS_ORIGINS` | adresy lokalne | lista adresów frontendu dopuszczonych przez CORS (JSON) |
| `AGRO_RATE_LIMIT_PER_MINUTE` | `120` | limit zapytań na minutę z jednego adresu (0 = wyłączony) |
| `AGRO_CACHE_DIR` | `data/cache` | katalog bufora (SQLite, BDOT10k, NMT) |
| `AGRO_NMT_GUGIK_TIMEOUT_S` | `4.5` | ile sekund analiza czeka na NMT 1 m, zanim użyje Copernicus DEM 30 m |
| `AGRO_OPEN_METEO_*` | publiczne API | adresy i opcjonalny klucz Open-Meteo (własna instancja lub plan komercyjny przy wdrożeniu) |

#### Raport PDF na Windows

Backend próbuje kolejno trzech rendererów:

1. **WeasyPrint**: pełny układ, wymaga bibliotek GTK/Pango;
2. **Matplotlib**: ta sama treść w prostszym układzie, działa na każdym Windows bez dodatkowych bibliotek;
3. **HTML**: gdy żaden renderer PDF nie działa.

Nagłówek odpowiedzi `X-Report-Renderer` mówi, który renderer zadziałał.

#### Testy

```bash
uv run pytest                 # backend, silnik fizyczny i moduł RL, bez sieci
cd frontend && pnpm test      # testy jednostkowe frontendu
cd frontend && pnpm test:live # przejście prawdziwej działki od numeru do wyniku (wymaga internetu)
```

#### Odtworzenie treningu modeli (opcjonalne, poza aplikacją)

Aplikacja używa gotowych wag z [artifacts/models/](artifacts/models/). Trening wymaga grupy zależności `train` (PyTorch CPU, Stable-Baselines3 i in.):

```bash
uv sync --group train --group dev
```

Polecenia dla obu modeli: [ml/rl_irrigator/README.md](ml/rl_irrigator/README.md) (agent nawadniania) i [ml/hydrofusion/README.md](ml/hydrofusion/README.md) (NDMI pod chmurami).

#### Skrypty pomocnicze (opcjonalne, dla serwera)

| Skrypt | Co robi |
| --- | --- |
| [scripts/probe_sources.py](scripts/probe_sources.py) | pobiera dane dla działki i wypisuje czas, tryb i kluczowe wartości każdego źródła |
| [scripts/prefetch_bdot10k.py](scripts/prefetch_bdot10k.py) | rozgrzewa bufor hydrografii BDOT10k dla wszystkich powiatów |
| [scripts/prefetch_copernicus_dem.py](scripts/prefetch_copernicus_dem.py) | pobiera kafle Copernicus DEM GLO-30 dla Polski (ok. 1,3 GB) |
| [scripts/prefetch_soilgrids.py](scripts/prefetch_soilgrids.py) | pobiera warstwy SoilGrids 250 m dla Polski (ok. 150 MB) |
| [scripts/prefetch_meteo_poland.py](scripts/prefetch_meteo_poland.py) | pobiera siatkę pogody dla Polski 2016–2026 (zapas na limit Open-Meteo) |
| [scripts/prefetch_map_tiles.py](scripts/prefetch_map_tiles.py) | rozgrzewa kafle ortofotomapy GUGiK |

Użytkownik końcowy nie uruchamia żadnego z nich. Bez nich dane są pobierane przy pierwszej analizie.

---

## 2. Opis podstawowych funkcjonalności

### Jak korzysta z niej rolnik

```text
  [1] Działka                 [2] Uprawa                   [3] Data
  klik na mapie, numer        jedna z 15 grup              dziś albo dowolny dzień
  TERYT albo obręb + numer    (np. kukurydza, truskawki)   od 1.01.2016
                                     │
                                     ▼
                         „Analizuj” → wynik w 5–16 s
```

Dane podstawowe działki (identyfikator, gmina, obręb, powierzchnia, granica) pojawiają się po ok. 2 s, kolejne kroki analizy doładowują się po kolei.

### Krok 0: wybór działek na mapie

- Ortofotomapa GUGiK z granicami i numerami działek ewidencyjnych (KIEG).
- Działkę dodaje się kliknięciem, numerem TERYT albo nazwą obrębu z numerem. Można wybrać kilka działek i dla każdej ustawić uprawę.
- Na mapie analizy działki mają kolor swojego stanu wody, a strefy wilgotności pokazują, gdzie na działce woda się zbiera, a gdzie szybko odpływa.
- Dla działki nierolniczej (las, woda, zabudowa wg mapy IUNG-PIB) serwis pokazuje komunikat zamiast analizy.

### Krok 1: ocena sytuacji wodnej

- **Stan wody** słownie i na skali od −5 (susza skrajna) przez 0 (optimum) do +5 (zalanie).
  - Strona sucha wynika z zapasu wody w strefie korzeniowej (bilans FAO-56), poprawionego o bardzo niski bilans opad − parowanie z 60 dni i spadek wilgotności łanu z satelity.
  - Strona mokra wynika z przesiąku i spływu z ostatnich 14–30 dni oraz dodatniego bilansu z 60 dni.
- **Werdykt suszy wg progów IUNG-PIB** (SMSR): klimatyczny bilans wodny (KBW) w 14 oknach sześciotygodniowych, porównany z progiem dla uprawy i kategorii gleby.
- **Ryzyko** z regułą weta: susza wg progów IUNG-PIB albo głęboki niedobór w fazie wrażliwej podnosi ryzyko niezależnie od pozostałych składowych. Ryzyko nie jest niższe, niż wskazuje stan wody.
- **Pewność danych**: spada, gdy źródło nie odpowiedziało, użyto zapasu albo model działa poza obszarem stosowalności.
- **WaterScore 0–100**: ogólna ocena warunków wodnych z pięciu składowych (zapas wody, KBW, satelita NDMI, pojemność gleby, teren).
- **Satelita czytany przez uprawę**: waga NDMI zależy od fazy uprawy i pokrycia gleby łanem w dniu zdjęcia (FAO-56, wzór 76).
  - Poza sezonem uprawy (goła gleba, ściernisko, spoczynek zimowy) NDMI nie wchodzi do oceny, a raport mówi dlaczego.
  - W sadach, krzewach, chmielu, truskawkach, warzywach i na łąkach waga jest ograniczona, bo piksel 10–20 m łączy rośliny z międzyrzędziem albo łąka jest koszona.
  - Gdy w ostatnich 15 dniach nie ma bezchmurnego zdjęcia, NDMI odtwarza model HydroFusion-PL z przedziałem niepewności.
- **Wykres stanu dzień po dniu** z prognozą na kolejne dni.
- **Historia 10 lat**: ten sam dzień w każdym z 10 poprzednich lat, czyli ocena historycznej podatności działki na niedobór wody.

### Krok 2: przyczyny niedoboru wody

Udział **klimatu, gleby, spływu z terenu i uprawy** w niedoborze, policzony scenariuszami „co gdyby” na tym samym bilansie (np. „co gdyby opad był jak średnia z 10 lat”, „co gdyby gleba miała pojemność gleby średniej”). Gdy niedoboru nie ma, serwis mówi to wprost i nie podaje procentów.

### Krok 3: działania retencyjne i agrotechniczne z symulacją „co jeśli”

Lista działań jest posortowana według **zysku wody na tej działce w najsuchszym sezonie z 10 lat** (w mm i w dniach zapasu). Symulator „co jeśli” przelicza bilans po wybraniu jednego lub kilku działań.

| Działanie | Rodzaj retencji | Grupa |
| --- | --- | --- |
| Międzyplon ścierniskowy lub ozimy | glebowa, agrotechnika | bez urządzeń wodnych |
| Mulcz z resztek pożniwnych | glebowa, agrotechnika | bez urządzeń wodnych |
| Uprawa uproszczona (bez orki) | glebowa, agrotechnika | bez urządzeń wodnych |
| Zwiększenie materii organicznej (obornik, kompost) | glebowa, agrotechnika | bez urządzeń wodnych |
| Pas buforowy (zadarnienie) w dolnej części działki | krajobrazowa | bez urządzeń wodnych |
| Zastawka (piętrzenie) na rowie melioracyjnym | krajobrazowa | **wymaga zgody Wód Polskich** |
| Sterowany odpływ z drenażu | krajobrazowa | **wymaga zgody Wód Polskich** |
| Mała retencja w naturalnym zagłębieniu terenu | krajobrazowa | **wymaga zgody Wód Polskich** |

- Działania wybiera się według warunków działki: spadku i zagłębień z modelu terenu oraz rowów i cieków z BDOT10k w pobliżu działki.
- Dla upraw wieloletnich (sady, krzewy, chmiel, truskawki) i łąk serwis nie proponuje międzyplonu ani uprawy bezorkowej.
- Każda zmiana urządzeń wodnych wymaga zgody PGW Wody Polskie i ma to oznaczenie w interfejsie i w raporcie.

### Krok 4: nawadnianie

- **W sezonie (wszystkie uprawy):** decyzja „podlewać czy nie”, dawka na dziś, plan na 14 dni z szansą podlewania w każdym dniu (prognoza zespołowa ECMWF, 51 wariantów pogody), woda w trzech warstwach gleby i ryzyko stresu.
- **Porównanie strategii z 10 sezonów:** brak nawadniania, kalendarz, reguła FAO-56 i agent RL. Tabela pokazuje stratę plonu, zużycie wody, przesiąk i dni stresu.
- **Poza sezonem:** tylko porównanie strategii.
- Agent startuje z zapasu wody policzonego w kroku 1, więc ocena i nawadnianie pokazują tę samą liczbę.

### Krok 5: raport

- **Tabela źródeł**: każde źródło z czasem pobrania i trybem (na żywo / bufor / zapas / brak danych).
- **Ograniczenia i niepewność** konkretnej analizy (np. „teren z Copernicus DEM 30 m, bo NMT 1 m nie odpowiedział”).
- Przycisk **„Pobierz raport PDF”** pobiera dwustronicowy PDF z serwera. Bez serwera przeglądarka drukuje stronę.
- Przykłady: [reports/](reports/).

### Ekrany aplikacji

| Ścieżka | Co robi |
| --- | --- |
| `/` | mapa Polski z wyszukiwarką, listą wybranych działek z uprawą przy każdej, datą i przyciskiem „Analizuj” |
| `/analiza` | wyniki dla wybranych działek: lista działek ze stanem wody, szczegóły aktywnej działki (kroki 1–5) i mapa |
| `/metoda` | metoda IUNG-PIB, skala stanu wody z liczbami dla ekspertów, ograniczenia |

### Obsługiwane uprawy

15 grup: 14 grup upraw z Systemu Monitoringu Suszy Rolniczej IUNG-PIB oraz łąki i pastwiska.

| Grunty orne | Sady i jagody | Użytki zielone |
| --- | --- | --- |
| Zboża ozime, Zboża jare, Kukurydza na ziarno, Kukurydza na kiszonkę, Rzepak i rzepik, Ziemniak, Burak cukrowy, Rośliny strączkowe, Warzywa gruntowe, Chmiel, Tytoń | Drzewa owocowe, Krzewy owocowe, Truskawki | Łąki i pastwiska (bez progu SMSR: działa sam bilans FAO-56, „Pewność” spada o jeden poziom) |

---

## 3. Skrócony opis architektury rozwiązania

```text
┌────────────────────────────── Przeglądarka rolnika ──────────────────────────────┐
│ frontend/  React 18 + TypeScript + MapLibre GL                                    │
│  • mapa: ortofotomapa GUGiK, KIEG, granice z ULDK, kategoria gleby IUNG WMS       │
│  • src/analysis/local.ts: uproszczona ocena IUNG, gdy serwer nie odpowiada        │
│  • src/api/: kontrakt z backendem (contract.ts)                                   │
└───────────────┬──────────────────────────────────────────────────────────────────┘
                │ /api, /rl  (pośrednik Vite; pośredniki /uldk i /om do GUGiK i Open-Meteo)
┌───────────────▼──────────────────────── Backend (FastAPI) ───────────────────────┐
│ src/backend                                                                       │
│  • clients/: 8 grup źródeł pobieranych równolegle, limit 25 s, bufor SQLite,      │
│    źródła zapasowe (NASA POWER, Copernicus DEM, SoilGrids)                        │
│  • analysis.py: kroki 1–5 KOWR  → AnalysisResponse                                │
│  • inference/hydrofusion.py: HydroFusion-PL (LightGBM), NDMI pod chmurami         │
│  • irrigation.py → ml/rl_irrigator (kowr_rl): agent PPO + plan 14 dni z ECMWF ENS │
│  • pdf/: raport PDF (WeasyPrint → Matplotlib → HTML)                              │
│  • security.py: limit zapytań, nagłówki bezpieczeństwa                            │
├───────────────────────────────────────────────────────────────────────────────────┤
│ src/hydro_core: silnik fizyczny, wspólny dla serwisu i treningu modeli            │
│  bilans FAO-56 + SCS-CN, ET₀, KBW i progi IUNG, teren (spadek, TWI, zagłębienia), │
│  stan wody, WaterScore, weta, przyczyny, ranking retencji, porady                 │
└───────────────────────────────────────────────────────────────────────────────────┘
```

### Przepływ jednej analizy

1. **≤ 2 s:** ULDK zwraca granicę działki, gminę i obręb. IUNG WMS sprawdza, czy to grunt rolny.
2. **Równolegle, limit 25 s:** pogoda (10 lat i prognoza), Sentinel-2, NMT, gleba, BDOT10k, IMGW. Źródło, które nie zdąży, dostaje w raporcie status „brak danych” i nie zatrzymuje analizy.
3. **Silnik fizyczny** liczy dzienny bilans na jednej osi czasu (historia i prognoza), bez zaglądania w przyszłość względem daty analizy.
4. **Kroki 1–5** korzystają z tego samego bilansu: ocena → przyczyny → ranking działań → nawadnianie → raport.
5. Gotowa analiza zostaje w pamięci przez 1 h, więc PDF i symulacja „co jeśli” nie liczą jej drugi raz.

### Moduły

| Katalog | Rola |
| --- | --- |
| [frontend/](frontend/) | aplikacja webowa (React, MapLibre), opis w [frontend/README.md](frontend/README.md) |
| [src/backend/](src/backend/) | API FastAPI, klienci źródeł danych, bufor, raport PDF, bezpieczeństwo |
| [src/hydro_core/](src/hydro_core/) | silnik fizyczny i reguły, konfiguracja w YAML (uprawy, działania, ocena) |
| [ml/rl_irrigator/](ml/rl_irrigator/) | agent nawadniania: środowisko, trening PPO, ewaluacja, API `/rl/*` |
| [ml/hydrofusion/](ml/hydrofusion/) | HydroFusion-PL: dane, trening i walidacja modelu NDMI |
| [artifacts/](artifacts/) | wagi modeli używane przez serwis i raporty walidacji |
| [scripts/](scripts/) | skrypty serwisowe (pomiar źródeł, rozgrzewanie bufora) |
| [tests/](tests/) | testy backendu i silnika |
| [promo/](promo/) | film z nagrań działającej aplikacji (Remotion) |

### API

| Metoda i ścieżka | Co robi |
| --- | --- |
| `GET /api/health` | status, wersja, lista upraw i źródeł |
| `GET /api/parcels/search` | wyszukiwanie działki po nazwie obrębu i numerze |
| `GET /api/parcel` | szybki pierwszy krok (≤ 2 s): granica i dane administracyjne działki |
| `POST /api/analyze` | pełna analiza, kroki 1–5 KOWR |
| `POST /api/what-if` | symulator „co jeśli” dla wybranych działań |
| `POST /api/zones` | wspólne strefy wilgotności dla 1–12 działek |
| `GET /api/terrain/status` | czy NMT 1 m dla działki jest już gotowy |
| `GET /api/weather/daily` | dzienna pogoda (bufor, zapas NASA POWER) |
| `GET /api/report.pdf` | raport PDF (HTML, gdy żaden renderer PDF nie działa) |
| `GET /api/registries` | wszystko, co jest zarejestrowane w silniku (cechy, składowe, reguły, źródła) |
| `POST /rl/recommend`, `POST /rl/compare` | moduł nawadniania: dawka, plan 14 dni, porównanie strategii |

### Decyzje architektoniczne

- **Jedno repozytorium, jeden kontrakt.** Cechy, bilans i schematy (`src/hydro_core`) są wspólne dla serwisu i treningu modeli, więc model w serwisie widzi te same dane co w treningu.
- **Lekki serwis.** Serwis nie ładuje PyTorcha. Agent PPO jest wyeksportowany do NumPy (`.npz`), HydroFusion-PL do natywnego pliku LightGBM. Oba liczą się na CPU w milisekundach.
- **Rejestry rozszerzeń.** Źródła danych, reguły, działania retencyjne, renderery raportu i dostawcy nawadniania rejestrują się w rejestrach. Nowe źródło albo nowe działanie to nowy wpis, bez zmiany potoku. Szczegóły i decyzje ADR: [docs/BACKEND_ARCHITECTURE.md](docs/BACKEND_ARCHITECTURE.md).
- **Zero makiet.** Frontend nie ma danych na sztywno. Każda liczba pochodzi z obliczenia na danych pobranych dla tej działki i daty. Gdy backend odpowiada, jego wynik zastępuje obliczenie przeglądarki.

---

## 4. Wykorzystane źródła i zakresy danych

| Kategoria | Źródło | Zakres | Gdzie |
| --- | --- | --- | --- |
| Przestrzenne | GUGiK ULDK | granica działki, gmina, obręb | frontend, backend, RL |
| Przestrzenne | GUGiK ortofotomapa (WMTS i WMS), KIEG (WMS) | podkład mapy, granice i numery działek | frontend |
| Przestrzenne | GUGiK wyszukiwarka adresów | wyszukiwanie miejscowości i adresów | frontend |
| Glebowo-rolnicze | IUNG-PIB WMS `Susza:katgl_landuse2024` | kategoria gleby I–IV, użytkowanie (odmowa dla lasu, wody, zabudowy) | frontend, backend, RL |
| Glebowo-rolnicze | IUNG-PIB progi SMSR (Rozp. MRiRW z 11.04.2019) | krytyczne KBW: 14 upraw × 4 kategorie gleby × 14 okien | frontend, backend |
| Meteorologiczne | Open-Meteo (ERA5 / ERA5-Land, ECMWF IFS, ECMWF ENS) | dzienny opad, ET₀ (FAO-56), Tmax; 10 lat, prognoza 16 dni, prognoza zespołowa | frontend, backend, RL |
| Meteorologiczne | NASA POWER (zapas za Open-Meteo) | dzienny opad, temperatura, wilgotność, wiatr, promieniowanie → ET₀ FAO-56 | backend (frontend przez pośrednika `/om`) |
| Meteorologiczne | IMGW-PIB (API danych publicznych) | najbliższa stacja: temperatura, odległość, różnica wysokości | backend (data bieżąca) |
| Satelitarne | Copernicus Sentinel-2 L2A Collection 1 (Earth Search STAC) | NDVI, NDMI działki, anomalia względem tego samego okresu w poprzednich latach | backend |
| Ukształtowanie terenu | GUGiK NMT 1 m (WCS), Copernicus DEM GLO-30 (zapas) | spadek, spływ, TWI, zagłębienia | backend |
| Glebowe | ISRIC SoilGrids v2.0 | piasek, ił, węgiel organiczny, woda użyteczna | backend, RL (plan 14 dni) |
| Hydrologiczne | GUGiK BDOT10k (pliki powiatów, opendata.geoportal.gov.pl) | rowy melioracyjne, cieki, kanały, wody powierzchniowe | backend |

- **Obszar:** cała Polska.
- **Okno czasowe:** 10 lat wstecz od dnia analizy. Analiza działa dla dowolnego dnia od 1.01.2016 do dziś.
- **Sentinel-2:** sceny dla Polski są w katalogu Earth Search od grudnia 2017 r. Rok 2022 ma w katalogu tylko pojedyncze sceny.

---

## 5. Dane LIVE vs PRE-FETCHED

> **Wynik i rekomendacje zawsze liczy aplikacja w chwili zapytania, z danych pobranych dla tej działki i tej daty.** Wcześniej pobrane są tylko tabela progów IUNG-PIB i dane do trenowania modeli.

| Źródło | Tryb | Uwagi |
| --- | --- | --- |
| ULDK, KIEG, ortofotomapa, IUNG WMS | **LIVE** + bufor | Każda nowa działka jest pobierana w chwili wyboru. Granica działki i kategoria gleby są buforowane (7 i 30 dni). |
| Open-Meteo (archiwum i prognoza) | **LIVE** + bufor | Pełne lata archiwum się nie zmieniają, więc przeglądarka i backend je buforują (bieżący rok: 6 h). Prognoza: 30 min (przeglądarka), 1 h (backend). |
| NASA POWER | **LIVE** (zapas) | Tylko gdy Open-Meteo odrzuci zapytania (limit) lub nie odpowie. Raport to zaznacza. |
| Sentinel-2, NMT / Copernicus DEM, SoilGrids, IMGW | **LIVE** + bufor | Sceny Sentinel-2 i model terenu działki są buforowane bez wygasania, bo się nie zmieniają. |
| BDOT10k | **LIVE** + bufor | Plik powiatu jest pobierany przy pierwszej analizie w danym powiecie. W `data/cache/bdot10k` jest 11 powiatów pobranych 21–22.11.2026. Skrypt `scripts/prefetch_bdot10k.py` pobiera kolejne. |
| Prognoza zespołowa ECMWF | **LIVE** + bufor (3 h) | Plan 14-dniowy. Dla dat od 2.04.2024 archiwum prognoz (dynamical.org), wcześniej lata analogiczne. |
| Progi IUNG SMSR | **PRE-FETCHED** (21.11.2026) | Tabela `frontend/src/data/iung-progi.json` z `susza.iung.pulawy.pl/progi`. Odświeżenie: `pnpm iung:progi`. |
| Dane treningowe RL | **PRE-FETCHED** (21.11.2026) | Open-Meteo 2016–2026, IUNG, SoilGrids, archiwum ECMWF ENS. Manifesty z datami: `ml/rl_irrigator/data/**/MANIFEST.json`. |
| Dane treningowe HydroFusion-PL | **PRE-FETCHED** (21–22.11.2026) | Próbki Sentinel-2 i SoilGrids w `ml/hydrofusion/data/` (ok. 2,3 MB), opis w [ml/hydrofusion/README.md](ml/hydrofusion/README.md). |
| Wagi modeli | **PRE-FETCHED** (wytrenowane 21–22.11.2026) | `artifacts/models/`. Modele liczą wynik na danych pobranych na żywo dla danej działki. |

---

## 6. Wykaz bibliotek, komponentów, modeli, usług zewnętrznych i narzędzi AI

### Biblioteki i komponenty

| Warstwa | Składniki |
| --- | --- |
| **Frontend** | React 18.3, React DOM, React Router 6.28, MapLibre GL 4.7, Vite 5.4 (+ @vitejs/plugin-react), TypeScript 5.6, Vitest 2.1 |
| **Backend** | Python 3.11, FastAPI, Uvicorn, Pydantic v2 (+ pydantic-settings), HTTPX, NumPy, SciPy, Shapely, pyproj, Rasterio (GDAL, affine), LightGBM, Matplotlib, Pillow, Jinja2, WeasyPrint, PyYAML |
| **Trening (grupa `train`, poza aplikacją)** | PyTorch (CPU), Stable-Baselines3, sb3-contrib (`MaskablePPO`), Gymnasium, TensorBoard, scikit-learn, Optuna, pandas, PyArrow, xarray, zarr, fsspec, aiohttp, tqdm, rich |
| **Narzędzia deweloperskie (grupa `dev`)** | pytest, pytest-asyncio, Ruff, mypy; menedżery pakietów uv i pnpm |
| **Film promocyjny (poza aplikacją)** | Remotion, opis w [promo/README.md](promo/README.md) |

### Modele uczenia maszynowego (własne, wytrenowane w trakcie Wydarzenia)

| Model | Typ | Co robi w aplikacji | Wagi | Walidacja |
| --- | --- | --- | --- | --- |
| **Agent nawadniania** `E6f_s2@dom` | PPO (`MaskablePPO`, Stable-Baselines3), eksport do NumPy | dawka wody na dziś i plan na 14 dni; środowisko to bilans FAO-56 z modelem plonu FAO-33 | `artifacts/models/ppo_irrigator_weights.npz`, karta modelu `ppo_irrigator_card.json` | [ml/rl_irrigator/reports/evaluation_holdout.md](ml/rl_irrigator/reports/evaluation_holdout.md) |
| **HydroFusion-PL** 1.0 | LightGBM, kwantyle 5/50/95% + kalibracja konformalna + obszar stosowalności (AoA) | odtwarza NDMI pod chmurami, gdy w ostatnich 15 dniach nie ma bezchmurnego zdjęcia; poza obszarem stosowalności się wyłącza | `artifacts/models/hydrofusion_*` (ok. 2,6 MB) | [ml/hydrofusion/README.md](ml/hydrofusion/README.md) |

Modele uczono wyłącznie na otwartych danych opisanych w sekcji 5.

### Usługi zewnętrzne i API (wszystkie bez konta i bez opłat)

| Dostawca | Adresy |
| --- | --- |
| GUGiK | `uldk.gugik.gov.pl`, `mapy.geoportal.gov.pl` (ortofotomapa, NMT WCS), `integracja.gugik.gov.pl` (KIEG), `services.gugik.gov.pl` (wyszukiwarka adresów), `opendata.geoportal.gov.pl` (BDOT10k) |
| IUNG-PIB | `sgeo.iung.pl` (WMS), `susza.iung.pulawy.pl` (progi SMSR) |
| Open-Meteo | `archive-api.open-meteo.com`, `api.open-meteo.com`, `ensemble-api.open-meteo.com`, `previous-runs-api.open-meteo.com` |
| NASA POWER | `power.larc.nasa.gov` |
| IMGW-PIB | `danepubliczne.imgw.pl` |
| Sentinel-2 | `earth-search.aws.element84.com` (katalog STAC) i AWS Open Data (pliki COG) |
| Copernicus DEM | `copernicus-dem-30m.s3.amazonaws.com` |
| ISRIC | `rest.isric.org` |
| Archiwum prognoz ECMWF ENS | `data.dynamical.org` (skrypty treningowe i opcjonalnie plan 14 dni) |

### Zakres i sposób zastosowania AI (pkt 5.1)

| Gdzie | Narzędzie / technika | Zakres |
| --- | --- | --- |
| **W aplikacji** | uczenie ze wzmocnieniem (PPO) | dobór dawki nawadniania; wynik porównany z regułą FAO-56 i kalendarzem na nowych lokalizacjach |
| **W aplikacji** | gradient boosting (LightGBM) | uzupełnienie brakującego pomiaru satelitarnego NDMI pod chmurami, z przedziałem niepewności |
| **W aplikacji** | modele językowe | **nie są używane.** Wszystkie teksty dla rolnika powstają z reguł na podstawie policzonych wartości |
| **Przy tworzeniu** | **Claude Code** (Anthropic) | pisanie i przegląd kodu, testy end-to-end, przegląd zgodności z wymaganiami KOWR, dokumentacja |
| **Przy tworzeniu** | **Google Antigravity** | środowisko programistyczne z agentem AI do pisania i refaktoryzacji kodu |
| **Przy tworzeniu** | generatory wideo | montaż filmu przewiduje klipy AI tylko z ujęciami pól i wody, bez ludzi i bez interfejsu; brakujący klip zastępuje zdjęcie na licencji otwartej; interfejs w filmie pochodzi wyłącznie z prawdziwych nagrań aplikacji |

Wynik pracy narzędzi AI sprawdzają testy automatyczne opisane w sekcji 9.

---

## 7. Licencje i warunki korzystania

### Kod i dokumentacja

Autorskie prawa majątkowe przechodzą na KOWR zgodnie z § 5 Regulaminu. Kod powstał w trakcie Wydarzenia.

### Biblioteki i komponenty

Wszystkie biblioteki mają licencje otwarte, które pozwalają na użycie komercyjne i przeniesienie praw do rozwiązania.

| Licencja | Składniki |
| --- | --- |
| **MIT** | React, React DOM, React Router, Vite, @vitejs/plugin-react, Vitest, @types/react, FastAPI, Pydantic, pydantic-settings, pyproj, LightGBM, PyYAML, GDAL (w kołach Rasterio), Stable-Baselines3, sb3-contrib, Gymnasium, Optuna, zarr, rich, pytest, Ruff, mypy, pnpm |
| **BSD** | MapLibre GL, NumPy, SciPy, Rasterio, affine, Shapely, Jinja2, WeasyPrint, Uvicorn, HTTPX, PyTorch, scikit-learn, pandas, fsspec |
| **Apache-2.0** | TypeScript, PyArrow, xarray, TensorBoard, aiohttp, pytest-asyncio; uv (MIT lub Apache-2.0) |
| **Inne** | Matplotlib (licencja oparta na PSF, zgodna z BSD), Pillow (MIT-CMU), tqdm (MPL-2.0 i MIT) |
| **Remotion** (tylko film, poza aplikacją) | Remotion License: bezpłatna dla osób prywatnych i zespołów do 3 osób zatrudnionych w firmie; przy komercyjnym użyciu przez większą organizację potrzebna licencja firmowa. Nie jest częścią aplikacji. |

### Dane

| Dane | Warunki korzystania |
| --- | --- |
| **GUGiK** (ULDK, ortofotomapa, KIEG, NMT, BDOT10k) | dane PZGiK udostępniane bez opłat do dowolnych celów (ustawa Prawo geodezyjne i kartograficzne, od 31.07.2020) |
| **IUNG-PIB** | publiczna usługa WMS SMSR; progi z Rozporządzenia MRiRW z 11.04.2019 |
| **Open-Meteo** | dane CC BY 4.0 (ERA5 Copernicus, ECMWF). Darmowe API jest do użytku niekomercyjnego (limit ok. 10 000 zapytań na dobę). Przy wdrożeniu potrzebna jest własna instancja (kod open source) albo plan komercyjny. |
| **NASA POWER** | dane publiczne NASA, bez ograniczeń, z podaniem źródła (NASA Langley Research Center POWER Project) |
| **IMGW-PIB** | dane publiczne zgodnie z regulaminem serwisu danepubliczne.imgw.pl; wymagane podanie źródła: „Źródłem pochodzenia danych jest Instytut Meteorologii i Gospodarki Wodnej – Państwowy Instytut Badawczy” |
| **Sentinel-2** | dane Copernicus wolne i otwarte (Copernicus Sentinel data [rok]) |
| **Copernicus DEM GLO-30** | licencja ESA/Airbus, bezpłatnie z podaniem źródła (© DLR e.V. 2010–2014 i © Airbus Defence and Space GmbH 2014–2018, Copernicus, UE i ESA) |
| **ECMWF ENS** (Open-Meteo, dynamical.org) | CC BY 4.0 |
| **ISRIC SoilGrids** | CC BY 4.0 |
| **Zdjęcia w interfejsie** | CC0, domena publiczna i CC BY, autorzy w [frontend/public/img/CREDITS.md](frontend/public/img/CREDITS.md) |

### Narzędzia AI

Claude Code (Anthropic) i Google Antigravity (Google) były używane wyłącznie jako narzędzia deweloperskie, na warunkach ich dostawców. Nie są częścią aplikacji i nie są z nią rozpowszechniane. Prawa do kodu wytworzonego z ich pomocą należą do użytkownika narzędzia zgodnie z warunkami dostawców.

### Dostępność dla użytkownika i koszty (pkt 5.4)

Rolnik **nie potrzebuje** płatnej licencji, specjalistycznego sprzętu, znajomości GIS, umiejętności programowania, samodzielnego przetwarzania danych przestrzennych ani instalowania oprogramowania. Wystarczy przeglądarka.

Prototyp nie korzysta z płatnych usług. Jedyny element, który przy wdrożeniu staje się płatny:

| Usługa | Dziś | Przy wdrożeniu w KOWR | Alternatywa |
| --- | --- | --- | --- |
| Open-Meteo API | darmowe API niekomercyjne | plan komercyjny Open-Meteo | własna instancja Open-Meteo (kod open source) na serwerze KOWR; zapasowo NASA POWER (bezpłatne, już wbudowane) |

---

## 8. Znane ograniczenia, skalowanie i bezpieczeństwo

### Ograniczenia techniczne

| Obszar | Ograniczenie | Jak to łagodzimy |
| --- | --- | --- |
| **Pogoda** | dane z siatki ok. 9 km (Open-Meteo), przy zapasie NASA POWER ok. 50 km, a nie pomiar na działce; lokalna burza może się różnić | raport podaje, które źródło użyto; IMGW-PIB pokazuje najbliższą stację |
| **Gleba** | kategorię I–IV wyznacza 5 punktów na działce (większość głosów); pojemność wodna to wartość reprezentatywna kategorii, a nie pomiar | SoilGrids uzupełnia skład gleby; raport podaje kategorię |
| **Bilans wody** | jednowarstwowy model FAO-56 („wiadro”) ze spływem SCS-CN; nie uwzględnia wód gruntowych, podsiąku ani drenażu rurkowego | opisane w raporcie jako czynniki nieuwzględnione |
| **Ewapotranspiracja** | IUNG-PIB liczy ją metodą Penmana, Open-Meteo metodą FAO-56 Penman-Monteith; wyniki mogą różnić się o kilka procent | progi IUNG-PIB stosujemy bez zmian; różnica opisana w metodzie |
| **Satelita** | NDMI porównujemy z tym samym okresem na tej samej działce w poprzednich latach, a wtedy mogła tam rosnąć inna uprawa (płodozmian); parametry reguły wagi NDMI wymagają weryfikacji z ekspertem, bo nie ma wzorcowych wartości NDMI dla każdej uprawy i fazy w Polsce | waga NDMI zależy od fazy i pokrycia łanem; poza sezonem NDMI nie wchodzi do oceny |
| **HydroFusion-PL** | R² 0,62, poniżej celu 0,78 | model tylko uzupełnia brakujące zdjęcia, ma przedział 90% i wyłącza się poza obszarem stosowalności |
| **Model terenu** | usługa NMT 1 m (WCS GUGiK) nie odpowiadała w testach 22.11.2026 nawet w 120 s | analiza czeka ok. 4,5 s, potem używa Copernicus DEM 30 m (spadki przybliżone); pobieranie 1 m trwa dalej w tle i kolejna analiza działki go użyje |
| **ULDK** | przy przeciążeniu GUGiK przekierowuje zapytania na stronę zastępczą | pośrednik frontendu ponawia zapytanie do 5 razy, backend buforuje granice działek |
| **Czas odpowiedzi** | zależy od łącza użytkownika i obciążenia serwisów publicznych | bufory, limit 25 s, „brak danych” zamiast zatrzymania analizy |
| **Zgody wodnoprawne** | każda zmiana urządzeń wodnych (zastawka, piętrzenie, zbiornik) wymaga zgody Wód Polskich | oznaczenie „Wymaga zgody Wód Polskich” przy każdym takim działaniu |
| **Parametry upraw** | Kc, p, Ky i terminy pochodzą z FAO-56/33 i wymagają weryfikacji z ekspertem dla warunków Polski | parametry w plikach YAML, łatwe do poprawienia bez zmiany kodu |

### Skalowanie (pkt 5.5)

- **Limity i dostępność źródeł:**
  - bufor SQLite (`data/cache`) dla granic działek, gleby, scen Sentinel-2, modelu terenu i pogody;
  - pobieranie równoległe z twardym limitem 25 s; źródło, które nie zdąży, ma w raporcie status „brak danych” zamiast zatrzymywać analizę;
  - źródła zapasowe: NASA POWER za Open-Meteo, Copernicus DEM za NMT 1 m, SoilGrids za IUNG;
  - limit zapytań na adres chroni wspólne limity darmowych usług.
- **Ruch:**
  - pogoda jest buforowana na poziomie oczka siatki Open-Meteo, więc jedno oczko obsługuje wiele działek;
  - frontend jest statyczny i można go wystawić na dowolnym hostingu lub CDN;
  - gotowa analiza (działka, uprawa, data) jest w pamięci przez 1 h, więc PDF i „co jeśli” nie liczą jej drugi raz;
  - przy większym ruchu: Redis lub PostGIS zamiast SQLite, kilka procesów Uvicorn za reverse proxy, nocne odświeżanie pogody dla oczek z aktywnymi działkami.
- **Aktualizacja danych:**
  - archiwum pogody i sceny satelitarne starsze niż 7 dni się nie zmieniają;
  - prognoza odświeża się co godzinę;
  - progi IUNG-PIB odświeża skrypt `pnpm iung:progi`;
  - BDOT10k i model terenu: pobierane przy pierwszej analizie w powiecie, opcjonalnie wcześniej skryptami z `scripts/`.
- **Koszty:**
  - jeden serwer obsługujący ponad 50 tys. analiz na dobę to szacunkowo ok. 350–500 zł/mies. (szacunek zespołu, [docs/SOLUTION_BLUEPRINT.md](docs/SOLUTION_BLUEPRINT.md));
  - do tego plan komercyjny Open-Meteo albo własna instancja;
  - pozostałe źródła są bezpłatne.
- **Integracja i rozwój:**
  - API REST z kontraktem Pydantic (Swagger pod `/docs`) pozwala podpiąć Rusałkę do innych systemów;
  - Etap 2: czujniki IoT wilgotności gleby jako dodatkowe źródło w rejestrze i sterowanie zastawkami przez agenta RL.

### Bezpieczeństwo i dane osobowe (pkt 5.6)

- **Brak danych osobowych:** aplikacja nie ma kont ani logowania i nie pyta o imię, adres czy PESEL. Wejście to numer działki (publiczne dane EGiB), uprawa i data.
  - Bufor serwera (`data/cache`) zawiera tylko dane publiczne: granice działek, pogodę, zdjęcia satelitarne, model terenu.
  - Przeglądarka zapisuje w `localStorage` tylko bufor pogody.
  - Limit zapytań trzyma adres klienta w pamięci najwyżej 60 s.
- **Walidacja wejścia (Pydantic):** format numeru TERYT sprawdzany przed zapytaniem do ULDK, długość pól tekstowych, współrzędne w granicach Polski, data od 1.01.2016 do dziś, uprawa z zamkniętej listy, najwyżej 12 działek w jednym zapytaniu o strefy.
- **Limit zapytań:** analiza, raport, „co jeśli”, strefy i moduł RL przyjmują najwyżej 120 zapytań na minutę z jednego adresu (`AGRO_RATE_LIMIT_PER_MINUTE`). Nadmiar dostaje HTTP 429 z nagłówkiem `Retry-After`.
  - Za pośrednikiem Vite wszystkie zapytania mają adres 127.0.0.1, więc limit działa łącznie.
  - Przy wdrożeniu za reverse proxy: `uvicorn --proxy-headers --forwarded-allow-ips=<adres proxy>`.
- **Nagłówki HTTP:** `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, `Permissions-Policy`; raport w wersji HTML ma `Content-Security-Policy`, który blokuje skrypty i zasoby zewnętrzne.
- **CORS:** tylko adresy z listy `AGRO_CORS_ORIGINS`, metody GET i POST, nagłówek `Content-Type`.
- **Wstrzykiwanie kodu:** bufor SQLite używa wyłącznie zapytań parametryzowanych; szablon raportu (Jinja2) ma automatyczne escapowanie HTML; parametry zapytań do usług zewnętrznych buduje HTTPX.
- **Pośredniki frontendu** (`/uldk`, `/om`) wysyłają zapytania tylko do stałych adresów GUGiK i Open-Meteo, więc nie działają jako otwarte proxy.
- **Sekrety:** opcjonalny klucz Open-Meteo tylko w pliku `.env` (w `.gitignore`), przechowywany jako `SecretStr`. Repozytorium nie zawiera kluczy.
- **Przy wdrożeniu:** HTTPS na reverse proxy, wyłączenie `/docs`, limit zapytań w proxy lub w Redis (przy kilku procesach Uvicorn).

---

## 9. Sposób weryfikacji wyników i metryki walidacji

### Testy automatyczne

| Zestaw | Polecenie | Liczba | Co sprawdza |
| --- | --- | --- | --- |
| **Backend, silnik i moduł RL** (bez sieci) | `uv run pytest` | 164 (+5 testów treningu RL, uruchamiane po `uv sync --group train`) | domknięcie bilansu masy, oś czasu bez zaglądania w przyszłość, teren, stan wody na osi susza–nadmiar, WaterScore i weta, przyczyny (bez procentów przy braku niedoboru), ranking retencji, waga NDMI zależna od uprawy i fazy, limit zapytań i nagłówki bezpieczeństwa, pełna analiza dla każdej z 15 upraw, porady, API kroków 1–5, odmowa dla działki nierolniczej, HydroFusion-PL, środowisko i polityka RL |
| **Frontend** | `pnpm test` | 50 | parsery ULDK i WKT, powierzchnia działki, 14 okien IUNG i zgodność tabeli progów z publikacją IUNG-PIB, KBW i skala stanu wody, zakresy pobierania pogody, bufor analiz, strefy łączone kilku działek |
| **Test na prawdziwych usługach** | `pnpm test:live` | 1 scenariusz | działka 040706_5.0050.78/2 od numeru do wyniku |

### Test end-to-end na prawdziwych usługach (22.11.2026)

- Działki z **6 województw**, daty **2018–2026**, **15 upraw**.
- Analiza trwa **5–16 s** (wymóg KOWR: ≤ 30 s).
- Las i zabudowa dostają komunikat zamiast analizy.
- Ocena i nawadnianie pokazują ten sam zapas wody w glebie.
- Przykładowe raporty: [reports/](reports/).

### Agent nawadniania RL: test na nowych lokalizacjach

**50 lokalizacji co najmniej 25 km od danych treningowych**, 15 upraw, lata 2025–2026, archiwalne prognozy ECMWF, **24 000 sezonów na strategię**.

| Porównanie | Woda | Plon względny | Wniosek |
| --- | --- | --- | --- |
| Agent RL vs podlewanie wg kalendarza | **74 mm** zamiast 135 mm na sezon (**−45%**) | **97,3%** zamiast 95,8% | mniej wody i wyższy plon |
| Agent RL vs reguła FAO-56 | **−8,5 mm** na sezon | **+0,12 pkt proc.** | obie różnice istotne (95% przedział ufności) |

Pełny raport: [ml/rl_irrigator/reports/evaluation_holdout.md](ml/rl_irrigator/reports/evaluation_holdout.md), dane: [artifacts/validation/rl_eval_holdout.json](artifacts/validation/rl_eval_holdout.json), opis decyzji: [docs/RL_IRRIGATION_DECISION_MODEL.md](docs/RL_IRRIGATION_DECISION_MODEL.md), prognoza 14 dni: [ml/rl_irrigator/reports/forecast14.md](ml/rl_irrigator/reports/forecast14.md).

### HydroFusion-PL: wilgotność łanu (NDMI) w dni zachmurzone

Walidacja 22.11.2026, opis w [ml/hydrofusion/README.md](ml/hydrofusion/README.md), metryki w [artifacts/validation/hydrofusion_eval.json](artifacts/validation/hydrofusion_eval.json).

| Zbiór testowy | RMSE | MAE | R² | Pokrycie przedziału 90% |
| --- | --- | --- | --- | --- |
| 50 nowych lokalizacji (12 175 obserwacji Sentinel-2) | **0,133** | 0,099 | 0,62 | **0,89** |
| Lata suszy 2018 i 2023 | 0,131 | – | 0,61 | 0,91 |

| Metoda porównawcza | RMSE |
| --- | --- |
| **HydroFusion-PL** | **0,133** |
| „Jak na ostatnim bezchmurnym zdjęciu” | 0,160 |
| „Jak w poprzednich latach” | 0,187 |

- Model ma o 29% mniejszy błąd niż historia działki. Bez przewagi nad obiema metodami porównawczymi model nie byłby wdrożony.
- 93% obserwacji leży w obszarze stosowalności. Poza nim serwis wraca do samej fizyki i obniża „Pewność danych”.
- **Cel ze specyfikacji (R² ≥ 0,78) nie jest osiągnięty.** Dlatego model tylko uzupełnia brakujące zdjęcia. Przyczyny opisuje raport modelu.

### Jak sprawdzić wynik samodzielnie

1. **Raport PDF** podaje każde źródło z czasem pobrania i trybem, więc każdą liczbę da się porównać z serwisem źródłowym.
2. **Werdykt suszy** liczy procedura SMSR IUNG-PIB (KBW w 14 oknach, progi z rozporządzenia MRiRW). Wynik można porównać z [susza.iung.pulawy.pl](https://susza.iung.pulawy.pl/) dla gminy działki.
3. **Strona `/metoda`** pokazuje skalę stanu wody i reguły z liczbami dla ekspertów.
4. **`GET /api/registries`** wypisuje wszystkie cechy, składowe oceny, reguły i źródła zarejestrowane w silniku.
5. **Plan (nie zrealizowany w trakcie Wydarzenia):** porównanie werdyktu dla działek z wykazem gminnym SMSR z lat 2020–2025.

---

## Podstawy naukowe

Metody odpowiadają materiałowi KOWR „Podstawy naukowe oceny sytuacji wodnej oraz zwiększania retencji wody w rolnictwie” i sekwencji: **ocena sytuacji wodnej → identyfikacja przyczyn → rekomendacje retencyjne → działania agrotechniczne → ewentualne nawadnianie**.

| Element Rusałki | Podstawa |
| --- | --- |
| Bilans wody w strefie korzeniowej, Kc, ET₀, pokrycie gleby łanem | Allen, Pereira, Raes, Smith (1998), *FAO Irrigation and Drainage Paper No. 56* |
| Reakcja plonu na niedobór wody (Ky) | Doorenbos, Kassam (1979), *FAO Irrigation and Drainage Paper No. 33* |
| KBW w 14 oknach i progi suszy dla upraw i gleb | Doroszewski i in. (2012), *Podstawy Systemu Monitoringu Suszy Rolniczej*, IUNG-PIB; Rozporządzenie MRiRW z 11.04.2019 |
| Spływ powierzchniowy | USDA-SCS (1972), metoda Curve Number; korekta na spadek: Huang i in. (2006) |
| Wskaźnik wilgotności terenu (TWI) | Beven, Kirkby (1979) |
| Wypełnianie zagłębień w modelu terenu | Barnes i in. (2014), Priority-Flood |
| Działania retencyjne | European Commission (2014, 2015), *Natural Water Retention Measures*; zasada „retain water where it falls” |
| Materia organiczna i pojemność wodna gleby | Minasny i in. (2017), *Soil carbon 4 per mille*, Geoderma 292 |
| Przedział niepewności NDMI | Romano i in. (2019), *Conformalized Quantile Regression* |
| Obszar stosowalności modelu (AoA) | Meyer, Pebesma (2021), *Predicting into unknown space?* |
| Agent nawadniania | Schulman i in. (2017), *Proximal Policy Optimization*; Ross, Bagnell (2014), uczenie przez naśladowanie |

---

## Struktura repozytorium

```text
agroretencja-kowr/
├── README.md                  ← ten plik (9 sekcji z pkt 4.2)
├── pyproject.toml, uv.lock    ← zależności Pythona (runtime, train, dev)
├── .env.example               ← opcjonalna konfiguracja backendu
├── frontend/                  ← aplikacja webowa (React, MapLibre)
├── src/
│   ├── backend/               ← API FastAPI, klienci źródeł, bufor, PDF, bezpieczeństwo
│   └── hydro_core/            ← silnik fizyczny i reguły (FAO-56, SCS-CN, IUNG, retencja)
├── ml/
│   ├── rl_irrigator/          ← agent nawadniania PPO: środowisko, trening, ewaluacja, API /rl
│   └── hydrofusion/           ← HydroFusion-PL: dane, trening, walidacja
├── artifacts/
│   ├── models/                ← wagi modeli używane przez serwis
│   └── validation/            ← metryki walidacji (JSON)
├── reports/                   ← raporty PDF wygenerowane przez prototyp
├── docs/                      ← koncepcja, architektura, model decyzji, materiał dla jury
├── scripts/                   ← skrypty serwisowe (pomiar źródeł, rozgrzewanie bufora)
├── tests/                     ← testy backendu i silnika
├── data/                      ← lokalny bufor (poza gitem, z wyjątkiem metadanych)
└── promo/                     ← film z nagrań działającej aplikacji (Remotion)
```

---

## Dalsza dokumentacja

| Dokument | Zawartość |
| --- | --- |
| [docs/SOLUTION_BLUEPRINT.md](docs/SOLUTION_BLUEPRINT.md) | koncepcja i metoda, ustalenia z mentorami KOWR |
| [docs/BACKEND_ARCHITECTURE.md](docs/BACKEND_ARCHITECTURE.md) | architektura backendu, rejestry rozszerzeń, decyzje ADR |
| [docs/RL_IRRIGATION_DECISION_MODEL.md](docs/RL_IRRIGATION_DECISION_MODEL.md) | model decyzji nawadniania (PPO vs FAO-56) |
| [docs/IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md) | plan i stan prac |
| [frontend/README.md](frontend/README.md) | aplikacja webowa: ekrany, obliczenia w przeglądarce, podpięcia backendu |
| [ml/rl_irrigator/README.md](ml/rl_irrigator/README.md) | agent nawadniania: dane, trening, ewaluacja |
| [ml/hydrofusion/README.md](ml/hydrofusion/README.md) | HydroFusion-PL: dane, zmienne, walidacja |
| [promo/README.md](promo/README.md) | film: nagrania aplikacji i montaż |
| [docs/materialy/Rusalka-material-dla-jury.pdf](docs/materialy/Rusalka-material-dla-jury.pdf) | dwustronicowy materiał dla jury |

---

## Zespół

**SolvronGen Racing In Space**

- Joanna Popielewska
- Marcel Musiałek
- Weronika Kaźmierczak
- Adam Basiewicz
- Iga Wolanin
- Piotr Wojdas

<div align="center">

**Rusałka** · HackNation 2026 · Krajowy Ośrodek Wsparcia Rolnictwa

</div>
