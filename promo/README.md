# Rusałka: film promocyjny

Film 16:9, 1920×1080, 30 kl./s, około 85 s. Montaż jest w Remotion (React). Interfejs w filmie pochodzi wyłącznie z prawdziwych nagrań działającej aplikacji. Klipy AI pokazują tylko pola i wodę, bez ludzi i bez interfejsu.

## Uruchomienie

```bash
pnpm install
pnpm capture      # nagrywa aplikację z http://localhost:4173 do public/rec/
pnpm studio       # podgląd i edycja w przeglądarce
pnpm render       # out/rusalka-promo.mp4
```

- `pnpm capture` wymaga działającego podglądu frontendu (`pnpm preview` w `frontend/`) i backendu na :8000.
- `pnpm render` i `pnpm studio` najpierw uruchamiają `capture/scan.mjs`. Skrypt sprawdza, które pliki opcjonalne są w `public/`, i zapisuje `src/assets.generated.json`.

## Pliki opcjonalne

Wstaw plik pod podaną nazwą i uruchom `pnpm render` ponownie. Brakujący klip AI zastępuje zdjęcie z licencją (`public/img/CREDITS.md`) z tą samą gradacją.

| Plik | Scena |
| --- | --- |
| `public/ai/01-susza.mp4` | otwarcie, susza |
| `public/ai/02-nadmiar.mp4` | nadmiar wody |
| `public/ai/03-z-gory.mp4` | ujęcie z góry, przejście w ortofotomapę |
| `public/ai/04-pole-swit.mp4` | tło sceny z telefonem |
| `public/ai/05-makro.mp4` | tło sceny „Metoda IUNG-PIB” |
| `public/ai/06-final.mp4` | zakończenie |
| `public/audio/voiceover.mp3` | lektor (głośność muzyki spada pod lektorem) |
| `public/audio/music.mp3` | muzyka (wyciszenie na końcu) |

## Prompty do klipów AI (Veo 3.1, Kling, Runway)

W każdym prompcie jest jedno ujęcie, jeden ruch kamery i zakaz ludzi i tekstu. Z klipu 8 s montaż bierze najlepsze 2–4 s.

**01-susza**
```
Cinematic 8-second shot, 24fps, anamorphic look. A drone descends slowly over a Polish maize field in a late-July drought: cracked pale soil between wilting rows, curled grey-green leaves, heat shimmer on the horizon. Slow push-in toward one row. Low golden-hour sun through warm haze, desaturated palette of burnt orange and dusty red. Audio: dry wind, cicadas, no music. No people, no hands, no animals, no text, no logos.
```

**02-nadmiar**
```
Cinematic 8-second shot, 24fps. Flat Polish farmland in early spring: standing water in wheel ruts and low hollows of a dark loam field, reflecting an overcast sky. Slow lateral dolly at knee height along the waterline. Cool diffused light, deep navy and slate-blue grade. Audio: soft rain, dripping water, wind. No people, no hands, no animals, no text, no logos.
```

**03-z-gory**
```
Perfectly top-down aerial shot, camera descending straight down toward a patchwork of narrow Polish farm parcels with field roads and drainage ditches. Constant smooth vertical descent, no rotation, no tilt. Neutral midday light, true-to-life orthophoto colours, sharp detail. Audio: soft wind. No people, no animals, no text, no logos.
```

**04-pole-swit**
```
Cinematic 8-second shot, 24fps, very shallow depth of field. Edge of a Polish maize field at dawn, rows softly out of focus, light mist low over the soil, dew on the nearest leaves. Locked-off camera with a barely perceptible slow push-in. Cool blue light warming to soft gold. Audio: morning birds, light wind. No people, no hands, no text, no logos, no devices.
```

**05-makro**
```
Macro slow motion. A water droplet falls onto dark crumbly loam and soaks in; moisture spreads between fine roots. Soft side light, rich earth browns with a fresh green accent. Audio: a single soft drop, then a deep low ambience. No people, no hands, no text.
```

**06-final**
```
Wide cinematic shot at sunset after rain: a lush green Polish field with a small retention pond in the corner reflecting an orange sky. The drone pulls back slowly and rises. Warm, calm, hopeful mood, gentle lens bloom. Audio: birdsong, a soft swell of ambient pads. No people, no animals, no text, no logos.
```

## Tekst lektora

Tekst odpowiada napisom w filmie. Lektor czyta spokojnie, około 2,3 słowa na sekundę.

| Scena | Tekst |
| --- | --- |
| Otwarcie | Susza nie zaczyna się, gdy liście więdną. Zaczyna się tygodnie wcześniej, pod ziemią. |
| Skrajności | Za mało wody. Albo za dużo. |
| Źródła | Dane o wodzie są rozproszone: gleby IUNG, satelita, pogoda i prognoza ECMWF, teren i rowy z GUGiK. Rolnik nie ma czasu ich składać. |
| Logo | Rusałka. Stan wody na każdej działce rolnej w Polsce. |
| Mapa | Znajdź swoją działkę: wpisz numer albo kliknij ją na mapie. |
| Analiza | Od suszy po zalanie. Strefy pokazują, gdzie woda ucieka, a gdzie się zbiera. |
| Dziś | Podlać czy poczekać? Rusałka mówi, co zrobić dziś i dlaczego, i prognozuje wodę w glebie na 14 dni. |
| Sezon | Cofnij się do suszy 2019 albo sprawdź prognozę. |
| Działania | Rusałka podpowiada, od czego zacząć, i liczy, ile wody zatrzymasz. |
| Uprawy | Inna uprawa to inny próg suszy. Rusałka pokazuje stan wody dla każdej uprawy i po wybranych działaniach. |
| Decyzja | Jak powstaje decyzja: dane publiczne, progi suszy IUNG-PIB, bilans wody FAO-56, satelita ważony fazą uprawy z HydroFusion-PL i agent RL. Każdy wynik ma podane źródło. |
| Telefon | Działa w telefonie, na miedzy. Bez instalacji. |
| Dowód | Metoda IUNG-PIB. Dane publiczne. Za darmo, dla każdej działki. |
| Co dalej | Widget z decyzją na dziś dla zapamiętanej działki, poranne powiadomienie, czujniki wilgotności w glebie i mapa suszy dla gminy. |
| Koniec | Rusałka. Woda na twoim polu, zanim jej zabraknie. |

## Struktura kodu

- `src/Promo.tsx`: kolejność scen, przejścia (przenikanie albo pasy skali stanu wody) i dźwięk.
- `src/scenes.tsx`: sceny i napisy.
- `src/parts.tsx`: ziarno i winieta, gradacja, napisy, znak Rusałki, przejście pasami skali, przybliżanie nagrań, ramka telefonu.
- `capture/record.mjs`: nagrywanie aplikacji (Playwright, CDP screencast) i `public/rec/manifest.json` ze znacznikami czasu i ramkami elementów do przybliżeń.

Licencja Remotion: bezpłatna dla osób prywatnych i firm do 3 osób.
