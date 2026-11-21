# Prognoza wody w glebie na 14 dni: ensemble ECMWF i trzy strefy gleby (E9, 21.11.2026)

**Cel:** prognozować na 14 dni do przodu, ile wody będzie w glebie działki, czy uprawa wejdzie w stres i kiedy trzeba będzie podlać. Prognoza ma być dokładna i uczciwie pokazywać swoją pewność.

**W skrócie:**
- **Pogoda na 14 dni** pochodzi z **51 członków ensemble ECMWF IFS** (prognoza zespołowa). Każdy członek to osobny, fizycznie spójny scenariusz opadu, temperatury i promieniowania.
- **Gleba to trzy strefy:** 0–28, 28–100 i 100–200 cm, jak warstwy ERA5-Land. Pojemność stref pochodzi z map **SoilGrids** (250 m) i jest dopasowana do kategorii IUNG-PIB.
  - Korzenie rosną w trakcie sezonu.
  - Parowanie z gleby i transpiracja roślin liczą się osobno.
- **Wynik to rozkład, nie jedna liczba.** Dla każdej strefy i każdego dnia podajemy klasę wody („bardzo sucho” … „pełny zapas”) z prawdopodobieństwem. Dla strefy korzeni podajemy zakres P10–P90, a do tego prawdopodobieństwo stresu uprawy.
- **Czysty test** (50 nowych lokalizacji, lata 2025–2026, 12 800 prognoz): prognoza wody w strefie korzeni jest **o 54% dokładniejsza** niż scenariusze z innych lat w dniu 1, **o 40% w dniu 6** i **o 29% w dniu 7**. Przewaga utrzymuje się przez wszystkie 14 dni.
- **Stres uprawy:** prognoza jest o 94% lepsza od klimatologii w dniu 1, o 72% w dniu 7 i o 49% w dniu 14.
- **Plan nawadniania na 14 dni** z ensemble trafia dni podlewania wyraźnie lepiej niż scenariusze z innych lat (E8).
  - Brier skill +0,53 wobec +0,33 w dniu 2 i +0,38 wobec +0,17 w dniu 4.
  - Zakres wody na 14 dni obejmuje rzeczywistość w 95% przypadków.

---

## 1. Dane

| Dane | Źródło | Do czego |
|---|---|---|
| Archiwalne prognozy ensemble ECMWF IFS ENS 0,25°: 51 członków, 15 dni, przebiegi 00 UTC | [dynamical.org](https://dynamical.org) (Zarr, CC BY 4.0), od 04.2024; przebiegi co tydzień 14 III – 23 X 2024–2026 dla 100 punktów | kalibracja (2024) i test (2025–2026) |
| Prognoza ensemble ECMWF na żywo (51 członków, 16 dni) | Open-Meteo Ensemble API (`ecmwf_ifs025`, CC BY 4.0) | rekomendacja na dziś |
| Profil gleby: woda przy pojemności polowej (`wv0033`) i w punkcie trwałego więdnięcia (`wv1500`), 6 warstw 0–200 cm | [ISRIC SoilGrids 2.0](https://soilgrids.org) (250 m, CC BY 4.0) | pojemność wodna stref |
| Opad i ET0 FAO-56, kategoria gleby IUNG-PIB | jak w E1–E8 | stan wody na dziś, pogoda „prawdziwa” w teście |
| Wilgotność gleby ERA5-Land, 4 warstwy | Open-Meteo Historical Weather API (`era5_land`) | niezależne sprawdzenie modelu (sekcja 6) |

Plan zaczynający się w dniu *t* używa przebiegu z 00 UTC dnia *t* − 1, czyli prognozy dostępnej rano. Dzień prognozy *k* (1…14) to dzień *t* + *k* − 1.

---

## 2. Model gleby: trzy strefy ([`kowr_rl/soil3.py`](../kowr_rl/soil3.py))

**Pojemność stref.**
- Woda dostępna w warstwie SoilGrids *j*: $\theta_{FC,j} - \theta_{WP,j}$ (% obj.).
- Pojemność strefy *z* sumuje warstwy w jej granicach: $AWC_z = \sum_j (\theta_{FC,j} - \theta_{WP,j}) \cdot \Delta z_{j \cap z}$.
- Całość jest przeskalowana tak, by $AWC_{0-28} + AWC_{28-100}$ równało się TAW kategorii IUNG-PIB. Dzięki temu model 3-strefowy zgadza się z bilansem, na którym uczono agenta, a SoilGrids dodaje kształt profilu i strefę głęboką.

**Korzenie i liście.**
- Głębokość korzeni $Z_r(t)$ rośnie od 0,2 m do maksymalnej głębokości uprawy (średnia z zakresu FAO-56, najwyżej 1,5 m), proporcjonalnie do rozwoju $K_c$.
- Uprawy ozime i wieloletnie mają pełne korzenie od początku sezonu.
- Udział strefy w strefie korzeni to $f_z = $ część strefy *z* sięgnięta przez korzenie.
- Udział okrywy roślinnej $f_T(t)$ rośnie z $K_c$.

**Jeden dzień** (`step3`):
1. **Infiltracja z góry na dół:** opad i dawka wypełniają strefę 0–28 cm, nadmiar spływa do 28–100 cm i dalej. To, co wypływa z 100–200 cm, to perkolacja.
2. **Parowanie z gleby:** $E = K_r (1 - f_T) K_c ET_0$, tylko ze strefy wierzchniej. $K_r = 1$ do 30% jej pojemności, potem maleje liniowo.
3. **Transpiracja:** $T = K_s f_T K_c ET_0$, gdzie $K_s$ to współczynnik stresu FAO-56 dla deficytu w zasięgu korzeni: $K_s = \frac{TAW_r - D_r}{(1-p)\,TAW_r}$, obcięty do [0, 1], przy $TAW_r = \sum_z f_z AWC_z$. Woda jest pobierana ze stref proporcjonalnie do ich zapasu w zasięgu korzeni.
4. **Bilans masy** zamyka się dokładnie (test na 200 losowych dniach).

**Stan na dziś.**
- Bilans biegnie od 1 sierpnia poprzedniego roku. Do siewu okrywa ma $K_c$ = 0,35 i korzenie 1,5 m, więc strefa głęboka pamięta suche lato i jesień.
- Od siewu model liczy sezon z $K_c$, korzeniami, okrywą uprawy i podanymi dawkami.

**Klasy wody:** względny zapas strefy $w = 1 - D/AWC$ dzielimy na 5 klas po 20 punktów procentowych: bardzo sucho < 20% ≤ sucho < 40% ≤ umiarkowany zapas < 60% ≤ wilgotno < 80% ≤ pełny zapas. Zgodnie z uwagą mentora prognoza podaje klasę z prawdopodobieństwem, a nie wartość z fałszywą precyzją.

---

## 3. Kalibracja pogody z ensemble ([`scripts/ens_calibrate.py`](../scripts/ens_calibrate.py))

- **ET0:** ensemble nie podaje ET0 FAO-56, więc liczymy ET0 Makkinka z temperatury 2 m i promieniowania krótkofalowego, $ET_0^{Mk} = 0{,}65 \frac{\Delta}{\Delta + \gamma} \frac{R_s}{\lambda}$. Potem kalibrujemy je liniowo do ET0 FAO-56, osobno dla każdego dnia wyprzedzenia: $ET_0 = a_k + b_k ET_0^{Mk}$, dopasowane na 2024.
- **Opad:** zostaje z ensemble bez zmian. Mapowanie kwantyli opadu sprawdziliśmy, ale nie poprawiło wyników na teście.

**Wynik na teście** (punkty holdout, 2025 / 2026). Liczby to CRPS skill wobec klimatologicznego „ensemble” z tych samych dni innych lat.

| Dzień prognozy | 1 | 2 | 3 | 4 | 5 | 7 | 10 |
|---|---|---|---|---|---|---|---|
| Opad 2025 | +0,51 | +0,41 | +0,31 | +0,29 | +0,07 | +0,06 | +0,02 |
| Opad 2026 | +0,52 | +0,38 | +0,40 | +0,24 | +0,27 | +0,08 | +0,08 |
| ET0 2025 (skalibrowane) | +0,51 | +0,44 | +0,42 | +0,25 | +0,29 | +0,14 | −0,04 |
| ET0 2026 (skalibrowane) | +0,48 | +0,41 | +0,40 | +0,22 | +0,28 | +0,05 | +0,07 |

Kalibracja ET0 usuwa systematyczne zaniżenie wzoru Makkinka: stosunek średniej prognozy do obserwacji w dniach 1–9 zmienia się z 0,86–0,92 na 0,99–1,07.

---

## 4. Prognoza wody w strefach ([`scripts/forecast14_study.py`](../scripts/forecast14_study.py))

**Układ testu:**
- 50 punktów holdout × lata 2025–2026 × 6 upraw (kukurydza, ziemniak, pszenica ozima, burak cukrowy, łąki i pastwiska, truskawki) × cotygodniowe przebiegi ECMWF = **12 800 prognoz 14-dniowych**.
- **Prawda:** ten sam model 3-strefowy na obserwowanej pogodzie.
- **Metody:**
  - **ENS**: 51 członków ECMWF;
  - **analog**: te same dni w 10 innych latach (metoda E8);
  - **persystencja**: stan z dziś przez 14 dni.
- **Postprocessing ENS** dopasowany wyłącznie na punktach treningowych w 2024 (6 250 prognoz, [`config/forecast14_postproc.json`](../config/forecast14_postproc.json)):
  - poszerzenie rozrzutu w dniu 1 ($\lambda$ = 1,25);
  - mieszanka z analogami w dniach 12–14 (waga ENS 0,75);
  - konformalne poszerzenie przedziału P10–P90 do pokrycia 80%.

**Woda w strefie korzeni:** CRPS skill ENS wobec analogów (dodatni = ENS lepszy).

| Dzień | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 | 11 | 12 | 13 | 14 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| ENS | **+0,54** | +0,50 | +0,46 | +0,44 | +0,42 | +0,40 | +0,29 | +0,25 | +0,22 | +0,21 | +0,17 | +0,13 | +0,09 | +0,05 |

- **Wobec persystencji** ENS jest dokładniejszy o 71% w dniu 1, 60% w dniu 7 i 53% w dniu 14. Gleba zmienia się szybko, a prognoza te zmiany oddaje.
- **Przedział P10–P90:** pokrywa prawdę w 78–87% przypadków w każdym dniu (cel 80%). Jego szerokość rośnie z 4 punktów procentowych w dniu 1 do 32 w dniu 14, czyli przedział sam pokazuje, jak rośnie niepewność.

**Każda strefa osobno:** CRPS skill ENS wobec analogów.

| Strefa | dzień 1 | dzień 4 | dzień 7 | dzień 10 | dzień 14 |
|---|---|---|---|---|---|
| 0–28 cm | +0,57 | +0,44 | +0,32 | +0,21 | 0,00 |
| 28–100 cm | +0,45 | +0,45 | +0,30 | +0,26 | +0,12 |
| 100–200 cm | +0,47 | +0,45 | +0,37 | +0,28 | +0,14 |

**Stres uprawy** ($K_s$ < 1): Brier score prawdopodobieństwa stresu.

| Dzień | 1 | 2 | 3 | 5 | 7 | 10 | 14 |
|---|---|---|---|---|---|---|---|
| ENS wobec klimatologii | +0,94 | +0,91 | +0,87 | +0,81 | +0,72 | +0,61 | +0,49 |
| ENS wobec analogów | +0,51 | +0,59 | +0,55 | +0,50 | +0,38 | +0,28 | +0,10 |
| ENS wobec persystencji | +0,74 | +0,75 | +0,69 | +0,66 | +0,60 | +0,53 | +0,46 |

---

## 5. Plan nawadniania na 14 dni z ensemble ([`scripts/plan_ens_study.py`](../scripts/plan_ens_study.py))

**Układ testu:**
- **Plan:** wdrożony agent planuje 14 dni na każdym z 51 członków ENS. Dzisiejsza decyzja widzi prognozę naprawdę wydaną dziś.
- **Prawdopodobieństwo podlewania w dniu *k*:** odsetek członków, w których plan przewiduje dawkę w tym dniu.
- **Odniesienie:** co agent naprawdę zrobi, gdy decyduje codziennie z nową prognozą na prawdziwej pogodzie.
- **Skala:** holdout 2025–2026, 6 upraw, 4 gleby, budżet 150 mm, czyli 51 200 planów.

| Dzień planu | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 10 |
|---|---|---|---|---|---|---|---|---|
| Brier skill: ENS | **+0,53** | **+0,49** | **+0,38** | **+0,29** | **+0,35** | **+0,23** | **+0,14** | **+0,12** |
| Brier skill: analogi (E8) | +0,33 | +0,26 | +0,17 | +0,10 | +0,16 | +0,07 | +0,09 | +0,06 |
| Trafione dni podlewania (recall, p ≥ 0,5): ENS | 60% | 52% | 46% | 33% | 35% | 27% | 18% | 10% |
| Trafione dni podlewania: analogi | 52% | 40% | 31% | 21% | 16% | 4% | 4% | 2% |
| Precyzja (p ≥ 0,5): ENS | 73% | 73% | 61% | 56% | 61% | 51% | 41% | 48% |

- **Brier skill** jest liczony wobec stałej częstości dni podlewania.
- **Woda na 14 dni:** średni błąd 5,0 mm. Zakres P10–P90 z 51 członków obejmuje rzeczywistość w **95%** przypadków (analogi: 91%).
- **Wniosek:** ensemble podwaja przewagę planu nad częstością bazową w dniach 2–7. Najlepiej widać to w dniach 4–7, gdzie analogi niewiele już wnoszą.

---

## 6. Niezależne sprawdzenie z ERA5-Land

*(sekcja uzupełniana po pobraniu danych ERA5-Land, [`scripts/era5_check.py`](../scripts/era5_check.py))*

---

## 7. W API: `plan_days=14`

`recommendation.plan_14d` zawiera:
- **`days`:** dawka na każdy dzień, prawdopodobieństwo podlewania z 51 członków ENS (`probability_source: "ecmwf_ens"`) i pewność dnia (dni 1–3 wysoka, 4–7 średnia, 8–14 niska);
- **`water_need_mm`:** oczekiwana woda na 14 dni z zakresem P10–P90;
- **`soil_zones`:**
  - pojemność trzech stref i udział korzeni;
  - stan na dziś;
  - dla każdego dnia: klasa strefy korzeni z prawdopodobieństwami klas, zakres P10–P90 zapasu wody, klasa każdej strefy z prawdopodobieństwami, prawdopodobieństwo stresu bez nawadniania i przy realizacji planu;
  - deficyt transpiracji w 14 dni bez nawadniania;
  - źródło pogody, źródło profilu gleby i skala danych.

**Źródła pogody:**
- **Dziś:** ensemble z Open-Meteo Ensemble API na żywo.
- **Daty historyczne:** archiwum dynamical.org przy `KOWR_RL_ENS_ARCHIVE=1` i zainstalowanych `xarray`/`zarr`, w pozostałych przypadkach scenariusze z tych samych dni innych lat.

Profil SoilGrids i ensemble są pobierane równolegle z pozostałymi danymi i tylko dla planu 14-dniowego.

**Przykład z testu.** Punkt holdout 51,49° N 16,71° E, kukurydza, gleba kategorii II (TAW 150 mm), prognoza z 26 VII 2025 (archiwalny przebieg ECMWF ENS). Stan na dziś: wierzchnia strefa po deszczu ma 82% zapasu („pełny zapas”), strefa 28–100 cm tylko 17% („bardzo sucho”), strefa korzeni łącznie 45%.

| Dzień | Strefa korzeni: klasa (prawdopodobieństwo) | Zapas P10–P90 | 0–28 cm | 28–100 cm | 100–200 cm | Stres bez nawadniania |
|---|---|---|---|---|---|---|
| 1 | umiarkowany zapas (1,00) | 43–46% | pełny zapas (0,65) | bardzo sucho (1,00) | umiarkowany (1,00) | 0,45 |
| 7 | umiarkowany zapas (0,43); sucho 0,16, wilgotno 0,22, pełny 0,20 | 36–91% | pełny zapas (0,76) | bardzo sucho (0,35) | umiarkowany (0,88) | 0,25 |
| 14 | umiarkowany zapas (0,36); sucho 0,27, wilgotno 0,25, pełny 0,12 | 31–89% | pełny zapas (0,77) | bardzo sucho (0,39) | umiarkowany (0,88) | 0,33 |

W dniu 1 prognoza jest praktycznie pewna. W kolejnych dniach członkowie ENS różnią się opadem, więc rozkład klas się rozszerza, a odpowiedź mówi to wprost zamiast podawać jedną liczbę.

---

## 8. Jak odtworzyć

```bash
cd ml/rl_irrigator
uv run --group train python scripts/fetch_soilgrids.py        # profile SoilGrids 100 punktów (ok. 25 min, limit 5 zapytań/min)
uv run --group train python scripts/fetch_ens_archive.py      # archiwum ECMWF ENS 2024-2026 (dynamical.org)
uv run --group train python scripts/ens_calibrate.py          # config/ens_calibration.json + reports/ens_calibration.json
uv run --group train python scripts/forecast14_study.py collect --dataset train_points
uv run --group train python scripts/forecast14_study.py collect --dataset holdout
uv run --group train python scripts/forecast14_study.py fit   # config/forecast14_postproc.json (tylko 2024)
uv run --group train python scripts/forecast14_study.py test  # reports/forecast14.json
uv run --group train python scripts/plan_ens_study.py         # reports/plan14_ens_holdout.json
uv run --group train python scripts/fetch_era5land.py         # ERA5-Land 50 punktów
uv run --group train python scripts/era5_check.py all         # reports/era5land_check.json
```
