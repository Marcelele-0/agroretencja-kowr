# Rusałka: frontend

Aplikacja webowa Rusałki: mapa działek, ocena suszy według metody IUNG-PIB i plan działań dla każdej wybranej działki. Działa w przeglądarce komputera i telefonu, bez instalacji.

## Uruchomienie

```bash
pnpm install
pnpm dev          # http://localhost:5173 (z pośrednikiem pogody /om)
pnpm build        # typecheck + statyczny build w dist/
pnpm preview      # serwuje dist/ na :4173 z tym samym pośrednikiem pogody
pnpm test         # testy jednostkowe (vitest)
pnpm test:live    # test na prawdziwych usługach: ULDK, IUNG, Open-Meteo
pnpm iung:progi   # pobiera na nowo tabelę progów IUNG do src/data/iung-progi.json
```

- `dist/` to statyczna strona. Serwer musi zwracać `index.html` dla ścieżek `/analiza` i `/metoda`.
- `pnpm dev` i `pnpm preview` pośredniczą w zapytaniach do Open-Meteo (`/om/archive`, `/om/forecast`). Dzięki temu limit Open-Meteo (ok. 600 zapytań na minutę z jednego adresu) liczy się dla serwera, a nie dla sieci użytkownika.
- Bez pośrednika (zwykły hosting statyczny) przeglądarka sama przechodzi na zapytania bezpośrednie.

## Ekrany

| Ścieżka | Co robi |
| --- | --- |
| `/` | Mapa Polski z ortofotomapą i granicami działek. Lewy panel: wyszukiwarka, lista wybranych działek z uprawą przy każdej, data i jeden przycisk „Analizuj”. Kliknięcie działki dodaje ją do listy, ponowne kliknięcie usuwa. Las, woda i zabudowa nie trafiają na listę. |
| `/analiza?dzialki=id~uprawa,…&aktywna=id&data=RRRR-MM-DD` | Lewy panel z danymi, mapa na resztę ekranu. Pasek daty, lista działek w stałej kolejności (stan wody i wykres 60 dni), pod nią szczegóły wybranej działki w ramce w kolorze jej stanu. Kliknięcie działki na mapie wybiera ją z listy. |
| `/metoda` | Metoda IUNG, skala stanu wody z liczbami dla ekspertów, ograniczenia. |

Stare linki z `dzialka`, `uprawa` i `inne` nadal działają.

## Co liczy przeglądarka

Wszystkie usługi wysyłają `Access-Control-Allow-Origin: *`:

| Usługa | Dane |
| --- | --- |
| GUGiK ULDK | granica działki (zapytanie z `srid=4326`), gmina, obręb, powierzchnia z granic |
| GUGiK ortofotomapa | WMTS z cache 30 dni, ostry WMS od zoomu 16 |
| GUGiK KIEG (WMS) | granice i numery działek od zoomu 15; powyżej ok. 1,8 m/px usługa zwraca pusty obraz |
| IUNG-PIB `Susza:katgl_landuse2024` | kategoria gleby I–IV, wykrycie lasu, wody i zabudowy |
| IUNG-PIB progi SMSR | krytyczne KBW: 14 upraw × 4 kategorie gleby × 14 okien (`src/data/iung-progi.json`) |
| Open-Meteo (archiwum ERA5 i prognoza) | dzienny opad i ET₀: sezon daty analizy, 105 dni przed nią, 60 dni przed tą samą datą w 10 latach, prognoza 15 dni. Około 11 małych zapytań, archiwum w cache `localStorage` |
| NASA POWER (zapas) | gdy archiwum Open-Meteo odrzuci zapytania (limit) albo nie odpowie: te same dni z NASA POWER, ET₀ liczone wzorem FAO-56 (`src/lib/nasaPower.ts`, ten sam wzór co backend). Tabela źródeł pokazuje, że użyto zapasu |

`src/analysis/local.ts` liczy z tych danych:
- KBW w 14 oknach IUNG i werdykt suszy dla uprawy i gleby;
- stan wody (skala −5…+5; w interfejsie tylko słowo i grafika), ryzyko i pewność;
- stan dzień po dniu: 45 dni przed datą i prognoza na kolejne dni;
- ten sam dzień albo miesiąc w 10 latach;
- przyczyny: klimat na tle średniej z 10 lat i gleba;
- wyjaśnienie wyniku: opad, parowanie, KBW, próg i reguła;
- listę działań posortowaną według zysku wody i symulację wybranych działań;
- prognozę opadu na 7 dni i poradę o nawadnianiu dla bieżącej daty.

To ocena uproszczona, używana tylko gdy backend nie odpowiada. Z backendem stan wody liczy serwer na tej samej osi (−5 susza … 0 optimum … +5 nadmiar): strona sucha z zapasu wody w strefie korzeniowej (bilans FAO-56), strona mokra z KBW 60 dni.

## Podpięcia backendu

Domyślnie (bez `VITE_API_URL`) frontend pyta backend pod tym samym adresem: `pnpm dev` i `pnpm preview` przekazują `/api` i `/rl` do `127.0.0.1:8000`. Inny serwer: `VITE_API_URL=https://…` (wzór w `.env.example`). `VITE_API_URL=` (puste) wyłącza podpięcia. Kontrakty są w `src/api/contract.ts`.

| Endpoint | Gdzie w interfejsie | Bez backendu |
| --- | --- | --- |
| `POST /api/analyze` | stan wody (`assessment.water_state`, `water_state_timeline`), składowe wzoru (`assessment.components`), przyczyny w %, ranking działań, strefy na mapie (`map_layers.zones`), źródła (`audit.sources`) | stan wody z KBW i progu IUNG, przyczyny klimat i gleba, działania z założeniami symulacji |
| `POST /rl/recommend` (`ml/rl_irrigator`, `plan_days: 14`) | decyzja o nawadnianiu, dawka dziś, plan 14 dni z szansą podlewania, woda w trzech strefach gleby i ryzyko stresu | porada z prognozy Open-Meteo |
| `POST /rl/compare` (`ml/rl_irrigator`) | strategie nawadniania z 10 sezonów: strata plonu, woda, przesiąk, dni stresu | brak tabeli |
| `GET /api/report.pdf` | przycisk „Pobierz raport PDF” | druk strony do PDF |

Backend (`/api/analyze`) i moduł RL obsługują wszystkie 15 upraw z listy. Gdy przyjdzie pełna analiza, karta nawadniania pokazuje jej odpowiedź RL (`irrigation.rl_recommendation`), liczoną od zapasu wody z oceny, zamiast osobnego wywołania `/rl/recommend`. Dzięki temu ocena i nawadnianie pokazują tę samą liczbę.

W trybie deweloperskim `&fixtures` w adresie analizy wczytuje próbkę odpowiedzi backendu (`src/api/fixtures.ts`). Build produkcyjny jej nie zawiera.

## Struktura

- `src/styles/`: `tokens.css` i `rusalka.css` z design systemu Rusałka (nie edytuj ręcznie), `app.css` (układ ekranów i elementy aplikacji).
- `src/ds/`: komponenty design systemu w TSX (`WaterBalance`, `StateTimeline`, `KbwWindows`, `YearCompare`, `DateBar`, …).
- `src/lib/`: klienty usług, geometria, daty, KBW, katalog działań.
- `src/analysis/`: obliczenia (`local.ts`) i hooki `useAnalysis`, `usePortfolio`.
- `src/pages/`: `MapHome`, `Analysis`, `Method`.
- `src/map/ParcelMap.tsx`: MapLibre z ortofotomapą, KIEG i działkami w kolorach stanu wody.
- `public/img/`: zdjęcia z licencjami w `CREDITS.md`.

## Testy

`pnpm test` sprawdza:
- parsery ULDK i WKT;
- powierzchnię działki;
- sezon i 14 okien IUNG;
- tabelę progów;
- KBW i skalę stanu wody;
- zakresy pobierania pogody;
- ET₀ zapasu NASA POWER (zgodność z backendem) i parser odpowiedzi NASA POWER.

`pnpm test:live` przechodzi prawdziwą działkę od numeru do wyniku. Pomiar z 21.11.2026: ok. 0,8 s.
