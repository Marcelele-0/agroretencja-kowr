# [ARCHIWUM E5] Ewaluacja agenta RL nawadniania (lata testowe 2025-2026), agent E5_s3

> Pierwszy test (E5). Aktualne wyniki: `evaluation_holdout.md` (czysty test E6) i `evaluation.md`.

Wygenerowano: 2026-11-21T14:48:34 przez `kowr_rl.evaluate`. Liczby pochodza z `reports/rl_eval_E5_test.json`.

**Uklad:** 50 punktow rolnych x lata 2025, 2026 x 4 uprawy x 4 kategorie gleby IUNG x 4 budzety wody = 6400 sezonow na strategie. Kazda strategia dostaje te same dane: pogode z archiwum Open-Meteo i **prawdziwe archiwalne prognozy ECMWF** (wyprzedzenie 1-7 dni; brakujace komorki: 0.29%). Wybrany agent: `E5_s3` (najlepszy na walidacji 2024). Przedzialy: 95% bootstrap po punktach i latach (2000 powtorzen). Przy tylko dwoch latach testowych przedzialy sa szerokie, bo obejmuja niepewnosc miedzy latami.

## Werdykt: agent RL **NIEGOTOWY - eksperymentalny**

Kryterium z planu: na tescie plon nie gorszy od reguly FAO o wiecej niz 1 pkt proc. przy mniejszym zuzyciu wody albo wyzszy plon przy tym samym budzecie. Sprawdzane lacznie i osobno dla kazdego poziomu budzetu. Zaostrzenie ustalone przed uruchomieniem testu: 'mniej wody' i 'wyzszy plon' musza byc istotne, czyli caly 95% przedzial bootstrap roznicy musi lezec po wlasciwej stronie zera. Sama srednia nie wystarcza. 'Wyzszy plon przy tym samym budzecie' liczy sie tylko wtedy, gdy agent nie zuzywa srednio wiecej wody niz FAO. Inaczej jest to wymiana wody na plon, a nie przewaga.

| Zakres | Delta plonu RL - FAO [pkt proc.] | Delta wody RL - FAO [mm] | Mniej wody, plon >= -1 pkt | Wyzszy plon | Wynik |
|---|---|---|---|---|---|
| wszystkie budzety | +0.09 [+0.06; +0.12] | -5.9 [-7.7; -4.2] | tak | tak | OK |
| 50 mm | +0.04 [-0.03; +0.09] | +0.1 [-0.3; +0.6] | nie | nie | NIE |
| 100 mm | +0.17 [+0.12; +0.24] | -2.3 [-3.7; -1.1] | tak | tak | OK |
| 150 mm | +0.25 [+0.19; +0.30] | -6.7 [-9.2; -4.0] | tak | tak | OK |
| 250 mm | -0.08 [-0.17; +0.02] | -14.7 [-17.9; -11.6] | tak | nie | OK |

RL vs **FAO + prognoza** (uczciwszy rywal, ktory tez korzysta z prognozy): delta plonu +0.13 [+0.05; +0.22] pkt proc., delta wody -2.8 [-3.6; -2.0] mm.

Domyslna strategia w module: **`fao_rule`** (`config/rl.yaml`, `default_policy`).

## Wyniki zbiorcze

![Strategie](figures/strategies_test_E5.png)

| Strategia | Plon sredni [95% CI] | Plon min | Plon 5. percentyl | Woda [mm] | Przyrost plonu na 100 mm [pkt proc.] | Dni stresu | Perkolacja [mm] |
|---|---|---|---|---|---|---|---|
| Bez nawadniania | 0.849 [0.808; 0.883] | 0.468 | 0.656 | 0 [0; 0] | - | 48 | 33 |
| Kalendarz | 0.954 [0.938; 0.966] | 0.554 | 0.803 | 138 [138; 138] | 7.6 | 20 | 99 |
| Regula FAO | 0.969 [0.955; 0.980] | 0.563 | 0.842 | 86 [78; 95] | 14.0 | 15 | 39 |
| FAO + prognoza | 0.969 [0.953; 0.980] | 0.563 | 0.842 | 83 [73; 93] | 14.4 | 16 | 38 |
| Agent RL | 0.970 [0.955; 0.981] | 0.563 | 0.839 | 80 [71; 90] | 15.1 | 14 | 38 |
| Wyrocznia (znana pogoda) | 0.973 [0.959; 0.984] | 0.563 | 0.842 | 75 [64; 86] | 16.7 | 12 | 35 |

**Ziarna losowe agenta RL** (te same sezony testowe):

| Przebieg | Plon | Woda [mm] | Perkolacja [mm] |
|---|---|---|---|
| `E5_s1` | 0.9693 | 78.0 | 37.0 |
| `E5_s2` | 0.9695 | 81.6 | 38.6 |
| `E5_s3` (wybrany) | 0.9699 | 80.2 | 38.3 |

**Odpornosc na rodzaj prognozy:** te same sezony z syntetyczna prognoza z treningu zamiast prawdziwej ECMWF (srednio z 3 ziaren szumu): RL plon 0.9696, woda 80.0 mm; FAO + prognoza plon 0.9687, woda 83.4 mm.

## Wedlug budzetu wody

![Budzet](figures/budget_curves_test_E5.png)

| Strategia | 50 mm: plon / woda | 100 mm: plon / woda | 150 mm: plon / woda | 250 mm: plon / woda |
|---|---|---|---|---|
| Bez nawadniania | 0.849 / 0 | 0.849 / 0 | 0.849 / 0 | 0.849 / 0 |
| Kalendarz | 0.901 / 50 | 0.945 / 100 | 0.974 / 150 | 0.994 / 250 |
| Regula FAO | 0.924 / 46 | 0.969 / 82 | 0.988 / 104 | 0.994 / 113 |
| FAO + prognoza | 0.925 / 45 | 0.969 / 80 | 0.987 / 100 | 0.993 / 107 |
| Agent RL | 0.925 / 46 | 0.971 / 80 | 0.990 / 97 | 0.993 / 98 |
| Wyrocznia (znana pogoda) | 0.926 / 43 | 0.974 / 73 | 0.993 / 89 | 0.999 / 94 |

## Wedlug uprawy i kategorii gleby (wszystkie budzety)

| Uprawa | Gleba (TAW) | FAO: plon / woda | Delta plonu RL-FAO [pkt proc.] | Delta wody RL-FAO [mm] | Sezony z RL gorszym o >1 pkt |
|---|---|---|---|---|---|
| Kukurydza na ziarno | 1 (110 mm) | 0.938 / 104 | +0.21 | -5.5 | 3% |
| Kukurydza na ziarno | 2 (150 mm) | 0.964 / 88 | +0.07 | -6.0 | 2% |
| Kukurydza na ziarno | 3 (185 mm) | 0.979 / 75 | +0.01 | -7.9 | 3% |
| Kukurydza na ziarno | 4 (220 mm) | 0.987 / 61 | +0.00 | -6.2 | 4% |
| Ziemniak (sredniopozny) | 1 (110 mm) | 0.924 / 119 | -0.14 | -7.0 | 13% |
| Ziemniak (sredniopozny) | 2 (150 mm) | 0.951 / 108 | +0.03 | -7.8 | 4% |
| Ziemniak (sredniopozny) | 3 (185 mm) | 0.965 / 100 | +0.03 | -9.1 | 4% |
| Ziemniak (sredniopozny) | 4 (220 mm) | 0.975 / 91 | +0.00 | -10.4 | 3% |
| Pszenica ozima (od wznowienia wegetacji) | 1 (110 mm) | 0.957 / 98 | +0.24 | -3.9 | 0% |
| Pszenica ozima (od wznowienia wegetacji) | 2 (150 mm) | 0.976 / 86 | +0.20 | -6.9 | 1% |
| Pszenica ozima (od wznowienia wegetacji) | 3 (185 mm) | 0.986 / 75 | +0.17 | -6.8 | 2% |
| Pszenica ozima (od wznowienia wegetacji) | 4 (220 mm) | 0.991 / 63 | +0.16 | -8.0 | 1% |
| Burak cukrowy | 1 (110 mm) | 0.957 / 100 | +0.14 | -2.2 | 2% |
| Burak cukrowy | 2 (150 mm) | 0.975 / 84 | +0.14 | -3.2 | 1% |
| Burak cukrowy | 3 (185 mm) | 0.985 / 69 | +0.13 | -1.6 | 0% |
| Burak cukrowy | 4 (220 mm) | 0.992 / 56 | +0.12 | -1.6 | 0% |

## Przykladowe sezony: agent RL vs regula FAO

![Sezony](figures/example_seasons_E5.png)

Gorny panel: niedobor wody w strefie korzeniowej (% TAW, wyzej = wilgotniej) i prog stresu RAW. Srodkowy: opad (szary) i dawki obu strategii. Dolny: woda skumulowana z plonem koncowym. Jeden z przykladow to celowo **najgorszy** przypadek agenta.

## Ograniczenia ewaluacji

- Plon to wynik modelu FAO-33 (jedna wartosc Ky na sezon), a nie pomiar polowy. Model nie rozroznia wrazliwosci faz rozwoju.
- Tylko dwa lata testowe, wiec niepewnosc miedzy latami jest duza (szerokie przedzialy).
- Kategorie gleby sa liczone w ukladzie czynnikowym (kazdy punkt w kazdej kategorii), wiec nie odpowiadaja rzeczywistemu rozkladowi gleb.
- Parametry upraw i TAW sa do weryfikacji z mentorem. Pogoda pochodzi z reanalizy na siatce ok. 9-25 km.
- Brak podsiaku wod gruntowych i splywu powierzchniowego w bilansie (model jednowarstwowy).

