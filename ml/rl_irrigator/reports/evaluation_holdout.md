# Ewaluacja agenta RL nawadniania: CZYSTY TEST na nowych lokalizacjach (holdout, lata 2025-2026)

Wygenerowano: 2026-11-21T17:24:28 przez `kowr_rl.evaluate`. Liczby pochodza z `artifacts/validation/rl_eval_holdout.json`.

**Zestaw holdout (E6):** 50 nowych punktow rolnych z siatki przesunietej o 0,35 st. (>= 25 km od kazdego punktu treningowego), nieuzywanych ani w treningu, ani przy wyborze modelu (protokol: PLAN.md, sekcja 5).

**Uklad:** 50 punktow rolnych x lata 2025, 2026 x 4 uprawy x 4 kategorie gleby IUNG x 4 budzety wody = 6400 sezonow na strategie. Kazda strategia dostaje te same dane: pogode z archiwum Open-Meteo i **prawdziwe archiwalne prognozy ECMWF** (wyprzedzenie 1-7 dni; brakujace komorki: 0.29%). Wybrany agent: `E6f_s2@dom` (wybrany na walidacji 2024). Przedzialy: 95% bootstrap po punktach i latach (2000 powtorzen).

## Werdykt: agent RL **GOTOWY**

Kryterium z planu: na tescie plon nie gorszy od reguly FAO o wiecej niz 1 pkt proc. przy mniejszym zuzyciu wody albo wyzszy plon przy tym samym budzecie. Sprawdzane lacznie i osobno dla kazdego poziomu budzetu. Zaostrzenie ustalone przed uruchomieniem testu: 'mniej wody' i 'wyzszy plon' musza byc istotne, czyli caly 95% przedzial bootstrap roznicy musi lezec po wlasciwej stronie zera. Sama srednia nie wystarcza. 'Wyzszy plon przy tym samym budzecie' liczy sie tylko wtedy, gdy agent nie zuzywa srednio wiecej wody niz FAO. Inaczej jest to wymiana wody na plon, a nie przewaga.

| Zakres | Delta plonu RL - FAO [pkt proc.] | Delta wody RL - FAO [mm] | Mniej wody, plon >= -1 pkt | Wyzszy plon | Wynik |
|---|---|---|---|---|---|
| wszystkie budzety | +0.23 [+0.14; +0.33] | -6.0 [-7.3; -5.1] | tak | tak | OK |
| 50 mm | +0.12 [+0.07; +0.17] | -0.9 [-1.4; -0.5] | tak | tak | OK |
| 100 mm | +0.26 [+0.12; +0.43] | -3.9 [-5.2; -2.8] | tak | tak | OK |
| 150 mm | +0.28 [+0.13; +0.44] | -8.2 [-10.0; -6.5] | tak | tak | OK |
| 250 mm | +0.27 [+0.20; +0.33] | -11.2 [-13.2; -9.6] | tak | tak | OK |

RL vs **FAO + prognoza** (uczciwszy rywal, ktory tez korzysta z prognozy): delta plonu +0.27 [+0.23; +0.32] pkt proc., delta wody -2.7 [-3.6; -1.7] mm.


RL vs **FAO male dawki + prognoza** (najlepsza prosta regula z E6) - ta sama regula decyzyjna:

| Zakres | Delta plonu [pkt proc.] | Delta wody [mm] | Wynik |
|---|---|---|---|
| wszystkie budzety | +0.11 [+0.09; +0.13] | -2.0 [-2.7; -1.3] | OK |
| 50 mm | +0.02 [+0.01; +0.04] | -0.4 [-0.8; -0.2] | OK |
| 100 mm | +0.05 [+0.03; +0.08] | -1.8 [-2.4; -1.2] | OK |
| 150 mm | +0.12 [+0.09; +0.16] | -2.8 [-3.9; -1.8] | OK |
| 250 mm | +0.23 [+0.18; +0.30] | -3.0 [-4.4; -1.6] | OK |


Sama regula **FAO male dawki + prognoza** vs regula FAO - ta sama regula decyzyjna:

| Zakres | Delta plonu [pkt proc.] | Delta wody [mm] | Wynik |
|---|---|---|---|
| wszystkie budzety | +0.12 [+0.03; +0.22] | -4.0 [-5.6; -2.7] | OK |
| 50 mm | +0.10 [+0.06; +0.14] | -0.5 [-1.0; -0.2] | OK |
| 100 mm | +0.21 [+0.08; +0.36] | -2.1 [-3.3; -0.8] | OK |
| 150 mm | +0.16 [+0.02; +0.30] | -5.4 [-7.8; -3.3] | OK |
| 250 mm | +0.04 [-0.04; +0.11] | -8.2 [-10.9; -6.0] | OK |

Domyslna strategia w module: **`rl`** (`config/rl.yaml`, `default_policy`).

## Wyniki zbiorcze

![Strategie](figures/strategies_holdout.png)

| Strategia | Plon sredni [95% CI] | Plon min | Plon 5. percentyl | Woda [mm] | Przyrost plonu na 100 mm [pkt proc.] | Dni stresu | Perkolacja [mm] |
|---|---|---|---|---|---|---|---|
| Bez nawadniania | 0.845 [0.809; 0.876] | 0.457 | 0.647 | 0 [0; 0] | - | 49 | 36 |
| Kalendarz | 0.951 [0.938; 0.963] | 0.556 | 0.796 | 138 [138; 138] | 7.8 | 21 | 101 |
| Regula FAO | 0.967 [0.956; 0.978] | 0.556 | 0.835 | 87 [79; 95] | 14.1 | 16 | 41 |
| FAO + prognoza | 0.967 [0.955; 0.978] | 0.556 | 0.836 | 84 [75; 93] | 14.6 | 16 | 40 |
| FAO male dawki + prognoza | 0.969 [0.956; 0.980] | 0.556 | 0.838 | 83 [75; 92] | 15.0 | 16 | 38 |
| Agent RL | 0.970 [0.957; 0.980] | 0.556 | 0.838 | 81 [73; 90] | 15.5 | 15 | 38 |
| Wyrocznia (znana pogoda) | 0.972 [0.959; 0.982] | 0.556 | 0.838 | 76 [66; 86] | 16.7 | 13 | 37 |

**Ziarna losowe agenta RL** (te same sezony testowe):

| Przebieg | Plon | Woda [mm] | Perkolacja [mm] |
|---|---|---|---|
| `E6f_s1@dom` | 0.9688 | 81.5 | 38.1 |
| `E6f_s2@dom` (wybrany) | 0.9697 | 80.9 | 38.4 |
| `E6f_s3@dom` | 0.9686 | 82.9 | 38.5 |

**Odpornosc na rodzaj prognozy:** te same sezony z syntetyczna prognoza z treningu zamiast prawdziwej ECMWF (srednio z 3 ziaren szumu): RL plon 0.9697, woda 81.3 mm; FAO + prognoza plon 0.9671, woda 84.1 mm.

## Wedlug budzetu wody

![Budzet](figures/budget_curves_holdout.png)

| Strategia | 50 mm: plon / woda | 100 mm: plon / woda | 150 mm: plon / woda | 250 mm: plon / woda |
|---|---|---|---|---|
| Bez nawadniania | 0.845 / 0 | 0.845 / 0 | 0.845 / 0 | 0.845 / 0 |
| Kalendarz | 0.897 / 50 | 0.942 / 100 | 0.972 / 150 | 0.994 / 250 |
| Regula FAO | 0.921 / 45 | 0.967 / 82 | 0.987 / 105 | 0.994 / 115 |
| FAO + prognoza | 0.921 / 45 | 0.967 / 80 | 0.987 / 100 | 0.993 / 109 |
| FAO male dawki + prognoza | 0.922 / 45 | 0.969 / 80 | 0.989 / 99 | 0.994 / 107 |
| Agent RL | 0.922 / 44 | 0.970 / 78 | 0.990 / 97 | 0.997 / 104 |
| Wyrocznia (znana pogoda) | 0.923 / 43 | 0.972 / 74 | 0.993 / 90 | 0.999 / 96 |

## Wedlug uprawy i kategorii gleby (wszystkie budzety)

| Uprawa | Gleba (TAW) | FAO: plon / woda | Delta plonu RL-FAO [pkt proc.] | Delta wody RL-FAO [mm] |
|---|---|---|---|---|
| Kukurydza na ziarno | 1 (110 mm) | 0.935 / 105 | +0.43 | -6.9 |
| Kukurydza na ziarno | 2 (150 mm) | 0.963 / 89 | +0.29 | -2.7 |
| Kukurydza na ziarno | 3 (185 mm) | 0.977 / 75 | +0.29 | +0.8 |
| Kukurydza na ziarno | 4 (220 mm) | 0.986 / 64 | +0.29 | +2.9 |
| Ziemniak (sredniopozny) | 1 (110 mm) | 0.921 / 119 | +0.73 | -9.8 |
| Ziemniak (sredniopozny) | 2 (150 mm) | 0.949 / 109 | +0.31 | -7.0 |
| Ziemniak (sredniopozny) | 3 (185 mm) | 0.964 / 101 | +0.25 | -6.1 |
| Ziemniak (sredniopozny) | 4 (220 mm) | 0.974 / 92 | +0.25 | -3.5 |
| Pszenica ozima (od wznowienia wegetacji) | 1 (110 mm) | 0.955 / 97 | -0.19 | -11.8 |
| Pszenica ozima (od wznowienia wegetacji) | 2 (150 mm) | 0.975 / 86 | +0.01 | -12.3 |
| Pszenica ozima (od wznowienia wegetacji) | 3 (185 mm) | 0.984 / 75 | +0.18 | -11.8 |
| Pszenica ozima (od wznowienia wegetacji) | 4 (220 mm) | 0.990 / 64 | +0.21 | -11.4 |
| Burak cukrowy | 1 (110 mm) | 0.955 / 100 | +0.19 | -8.7 |
| Burak cukrowy | 2 (150 mm) | 0.974 / 85 | +0.13 | -5.2 |
| Burak cukrowy | 3 (185 mm) | 0.985 / 73 | +0.15 | -4.0 |
| Burak cukrowy | 4 (220 mm) | 0.991 / 58 | +0.18 | +0.5 |

## Przykladowe sezony: agent RL vs regula FAO

![Sezony](figures/example_seasons_holdout.png)

Gorny panel: niedobor wody w strefie korzeniowej (% TAW, wyzej = wilgotniej) i prog stresu RAW. Srodkowy: opad (szary) i dawki obu strategii. Dolny: woda skumulowana z plonem koncowym.

## Zalozenia ewaluacji

- Wszystkie strategie sa liczone tym samym bilansem FAO-56 i ta sama funkcja plonu FAO-33, na tych samych danych pogodowych i prognozach.
- Uklad czynnikowy: kazdy punkt w kazdej kategorii gleby IUNG-PIB, zeby porownac strategie na wszystkich glebach.
- Parametry upraw z FAO-56/33 (`config/crops.yaml`), pojemnosc wodna gleby z kategorii IUNG-PIB (`config/soils.yaml`).

