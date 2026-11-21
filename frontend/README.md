# Rusałka: frontend

Aplikacja webowa Rusałki: mapa działek, ocena suszy według metody IUNG-PIB i plan działań. Działa w przeglądarce komputera i telefonu, bez instalacji.

## Uruchomienie

```bash
pnpm install
pnpm dev          # http://localhost:5173
pnpm build        # typecheck + statyczny build w dist/
pnpm test         # testy jednostkowe (vitest)
pnpm test:live    # test na prawdziwych usługach: ULDK, IUNG, Open-Meteo
pnpm iung:progi   # pobiera na nowo tabelę progów IUNG do src/data/iung-progi.json
```

`dist/` to statyczna strona. Każdy hosting statyczny ją obsłuży. Serwer musi zwracać `index.html` dla ścieżek `/analiza` i `/metoda`.

## Co liczy przeglądarka

Wszystkie usługi wysyłają `Access-Control-Allow-Origin: *`, więc frontend odpytuje je bezpośrednio:

| Usługa | Dane |
| --- | --- |
| GUGiK ULDK | granica działki (EPSG:2180), gmina, obręb, powierzchnia z granic |
| GUGiK ortofotomapa (WMTS z cache, ostry WMS od zoomu 16) i KIEG (WMS) | podkład mapy, granice i numery działek od zoomu 15 |
| IUNG-PIB `Susza:katgl_landuse2024` | kategoria gleby I–IV, wykrycie lasu, wody i zabudowy |
| IUNG-PIB progi SMSR | krytyczne KBW dla 14 upraw × 4 gleb × 14 okien (`src/data/iung-progi.json`) |
| Open-Meteo (archiwum ERA5 i prognoza) | dzienny opad i ET₀: sezon daty analizy i 60 dni przed tą samą datą w 10 latach (ok. 10 małych zapytań z cache w `localStorage`), prognoza na 7 dni |

Z tych danych `src/analysis/local.ts` liczy:
- KBW w 14 oknach IUNG i werdykt suszy;
- stan wody od −5 do +5, ryzyko i pewność;
- ten sam dzień albo miesiąc w 10 latach;
- przyczyny: klimat na tle średniej z 10 lat i gleba;
- wyjaśnienie wyniku: opad, parowanie, KBW, próg i reguła stopnia;
- „co jeśli”: zatrzymana woda i mniejsze parowanie przeliczone na tych samych danych;
- porównanie kilku działek (`&inne=id1,id2`, przycisk na mapie analizy);
- prognozę opadu na 7 dni i poradę o nawadnianiu dla bieżącej daty.

Skala od −5 do +5 wynika tymczasowo z KBW i progu IUNG. Backend zastąpi ją bilansem FAO-56.

## Podpięcia backendu

Adres backendu podaje `VITE_API_URL` (wzór w `.env.example`). Puste pole wyłącza podpięcia. Kontrakty są w `src/api/contract.ts`.

| Endpoint | Gdzie w interfejsie | Bez backendu |
| --- | --- | --- |
| `POST /api/analyze` | przyczyny w %, ranking retencji z efektami, strefy na mapie, audyt źródeł | przyczyny klimat i gleba, katalog działań bez liczb |
| `POST /rl/recommend` (gałąź `feat/RL`) | karta nawadniania z dawką i planem 7 dni | porada z prognozy Open-Meteo |
| `GET /api/report.pdf` | przycisk „Pobierz raport PDF” | druk strony do PDF |

W trybie deweloperskim `&fixtures` w adresie analizy wczytuje próbkę odpowiedzi backendu (`src/api/fixtures.ts`). Build produkcyjny nie zawiera próbki.

## Struktura

- `src/styles/`: `tokens.css` i `rusalka.css` z design systemu Rusałka (nie edytuj ręcznie), `app.css` (układ ekranów).
- `src/ds/`: komponenty design systemu w TSX.
- `src/lib/`: klienty usług, geometria, daty, KBW.
- `src/analysis/`: obliczenia i hook `useAnalysis`.
- `src/pages/`: mapa (`/`), analiza (`/analiza?dzialka=…&uprawa=…&data=…`), metoda (`/metoda`).
- `public/img/`: zdjęcia z licencjami w `CREDITS.md`.
