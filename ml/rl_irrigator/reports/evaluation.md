# Ewaluacja agenta RL nawadniania (lata testowe 2025-2026)

Wygenerowano: 2026-11-21T20:04:37 przez `kowr_rl.evaluate`. Liczby pochodza z `artifacts/validation/rl_eval.json`.

**Test na nowych lokalizacjach (holdout):** `reports/evaluation_holdout.md`.

**Uklad:** 50 punktow rolnych x lata 2025, 2026 x 4 uprawy x 4 kategorie gleby IUNG x 4 budzety wody = 24000 sezonow na strategie. Kazda strategia dostaje te same dane: pogode z archiwum Open-Meteo i **prawdziwe archiwalne prognozy ECMWF** (wyprzedzenie 1-7 dni; brakujace komorki: 0.38%). Wybrany agent: `E6f_s2@dom` (wybrany na walidacji 2024). Przedzialy: 95% bootstrap po punktach i latach (2000 powtorzen).

## Werdykt: agent RL **GOTOWY**

Kryterium z planu: na tescie plon nie gorszy od reguly FAO o wiecej niz 1 pkt proc. przy mniejszym zuzyciu wody albo wyzszy plon przy tym samym budzecie. Sprawdzane lacznie i osobno dla kazdego poziomu budzetu. Zaostrzenie ustalone przed uruchomieniem testu: 'mniej wody' i 'wyzszy plon' musza byc istotne, czyli caly 95% przedzial bootstrap roznicy musi lezec po wlasciwej stronie zera. Sama srednia nie wystarcza. 'Wyzszy plon przy tym samym budzecie' liczy sie tylko wtedy, gdy agent nie zuzywa srednio wiecej wody niz FAO. Inaczej jest to wymiana wody na plon, a nie przewaga.

| Zakres | Delta plonu RL - FAO [pkt proc.] | Delta wody RL - FAO [mm] | Mniej wody, plon >= -1 pkt | Wyzszy plon | Wynik |
|---|---|---|---|---|---|
| wszystkie budzety | +0.12 [+0.06; +0.19] | -8.5 [-10.0; -7.2] | tak | tak | OK |
| 50 mm | +0.10 [+0.06; +0.13] | -1.7 [-2.5; -1.0] | tak | tak | OK |
| 100 mm | +0.16 [+0.05; +0.29] | -6.5 [-8.2; -4.7] | tak | tak | OK |
| 150 mm | +0.13 [+0.04; +0.22] | -11.7 [-13.8; -9.5] | tak | tak | OK |
| 250 mm | +0.11 [+0.07; +0.15] | -14.0 [-15.6; -12.7] | tak | tak | OK |

RL vs **FAO + prognoza** (uczciwszy rywal, ktory tez korzysta z prognozy): delta plonu +0.16 [+0.14; +0.18] pkt proc., delta wody -5.4 [-6.0; -4.9] mm.


RL vs **FAO male dawki + prognoza** (najlepsza prosta regula z E6) - ta sama regula decyzyjna:

| Zakres | Delta plonu [pkt proc.] | Delta wody [mm] | Wynik |
|---|---|---|---|
| wszystkie budzety | +0.00 [-0.02; +0.02] | -4.2 [-4.9; -3.5] | OK |
| 50 mm | -0.00 [-0.02; +0.01] | -1.1 [-1.6; -0.6] | OK |
| 100 mm | -0.02 [-0.05; +0.01] | -3.7 [-4.8; -2.7] | OK |
| 150 mm | -0.00 [-0.03; +0.02] | -5.6 [-6.8; -4.4] | OK |
| 250 mm | +0.03 [-0.01; +0.07] | -6.5 [-7.6; -5.4] | OK |


Sama regula **FAO male dawki + prognoza** vs regula FAO - ta sama regula decyzyjna:

| Zakres | Delta plonu [pkt proc.] | Delta wody [mm] | Wynik |
|---|---|---|---|
| wszystkie budzety | +0.12 [+0.06; +0.19] | -4.2 [-5.4; -3.3] | OK |
| 50 mm | +0.10 [+0.07; +0.13] | -0.6 [-1.0; -0.3] | OK |
| 100 mm | +0.18 [+0.09; +0.30] | -2.7 [-3.8; -1.8] | OK |
| 150 mm | +0.13 [+0.05; +0.22] | -6.1 [-7.6; -4.7] | OK |
| 250 mm | +0.08 [+0.03; +0.12] | -7.5 [-9.2; -6.1] | OK |

Domyslna strategia w module: **`rl`** (`config/rl.yaml`, `default_policy`).

## Wyniki zbiorcze

![Strategie](figures/strategies_test.png)

| Strategia | Plon sredni [95% CI] | Plon min | Plon 5. percentyl | Woda [mm] | Przyrost plonu na 100 mm [pkt proc.] | Dni stresu | Perkolacja [mm] |
|---|---|---|---|---|---|---|---|
| Bez nawadniania | 0.866 [0.825; 0.899] | 0.467 | 0.672 | 0 [0; 0] | - | 47 | 27 |
| Kalendarz | 0.960 [0.946; 0.972] | 0.542 | 0.816 | 135 [135; 135] | 7.0 | 18 | 93 |
| Regula FAO | 0.973 [0.959; 0.984] | 0.563 | 0.853 | 82 [73; 92] | 13.1 | 15 | 34 |
| FAO + prognoza | 0.973 [0.958; 0.984] | 0.563 | 0.853 | 79 [69; 90] | 13.6 | 15 | 32 |
| FAO male dawki + prognoza | 0.974 [0.960; 0.985] | 0.563 | 0.853 | 78 [68; 88] | 14.0 | 15 | 31 |
| Agent RL | 0.974 [0.960; 0.985] | 0.563 | 0.854 | 74 [64; 84] | 14.8 | 15 | 30 |
| Wyrocznia (znana pogoda) | 0.977 [0.963; 0.987] | 0.563 | 0.854 | 69 [59; 81] | 16.0 | 12 | 29 |

**Ziarna losowe agenta RL** (te same sezony testowe):

| Przebieg | Plon | Woda [mm] | Perkolacja [mm] |
|---|---|---|---|
| `E6f_s2@dom` (wybrany) | 0.9743 | 73.5 | 30.5 |
| `E7_s2@domc` | 0.9742 | 76.3 | 31.3 |

**Odpornosc na rodzaj prognozy:** te same sezony z syntetyczna prognoza z treningu zamiast prawdziwej ECMWF (srednio z 3 ziaren szumu): RL plon 0.9743, woda 73.6 mm; FAO + prognoza plon 0.9728, woda 79.3 mm.

## Wedlug budzetu wody

![Budzet](figures/budget_curves_test.png)

| Strategia | 50 mm: plon / woda | 100 mm: plon / woda | 150 mm: plon / woda | 250 mm: plon / woda |
|---|---|---|---|---|
| Bez nawadniania | 0.866 / 0 | 0.866 / 0 | 0.866 / 0 | 0.866 / 0 |
| Kalendarz | 0.913 / 50 | 0.956 / 100 | 0.979 / 149 | 0.994 / 241 |
| Regula FAO | 0.935 / 45 | 0.974 / 79 | 0.989 / 98 | 0.995 / 106 |
| FAO + prognoza | 0.935 / 44 | 0.974 / 77 | 0.988 / 94 | 0.994 / 101 |
| FAO male dawki + prognoza | 0.936 / 44 | 0.975 / 76 | 0.990 / 92 | 0.995 / 99 |
| Agent RL | 0.936 / 43 | 0.975 / 73 | 0.990 / 86 | 0.996 / 92 |
| Wyrocznia (znana pogoda) | 0.937 / 41 | 0.977 / 69 | 0.993 / 81 | 0.999 / 87 |

## Wedlug uprawy (wszystkie gleby i budzety)

| Uprawa | FAO: plon / woda | Delta plonu RL-FAO [pkt proc.] | Delta wody RL-FAO [mm] |
|---|---|---|---|
| Kukurydza na ziarno | 0.967 / 82 | +0.34 | -0.8 |
| Ziemniak (sredniopozny) | 0.954 / 105 | +0.36 | -6.3 |
| Pszenica ozima (od wznowienia wegetacji) | 0.978 / 80 | +0.05 | -12.1 |
| Burak cukrowy | 0.977 / 77 | +0.18 | -3.4 |
| Zboza jare (jeczmien, pszenica jara) | 0.973 / 82 | +0.24 | -10.2 |
| Kukurydza na kiszonke | 0.969 / 80 | +0.32 | -2.8 |
| Rzepak ozimy (od wznowienia wegetacji) | 0.984 / 69 | -0.29 | -17.0 |
| Rosliny straczkowe (groch, bobik na nasiona) | 0.976 / 85 | +0.20 | -12.0 |
| Warzywa gruntowe (kapustne) | 0.984 / 70 | +0.11 | -6.2 |
| Chmiel | 0.984 / 72 | -0.04 | -11.4 |
| Tyton | 0.989 / 62 | -0.05 | -11.9 |
| Drzewa owocowe (sad jabloniowy) | 0.929 / 112 | +0.22 | -0.3 |
| Krzewy owocowe (porzeczka, malina) | 0.975 / 83 | +0.14 | -5.2 |
| Truskawki | 0.984 / 92 | +0.02 | -22.5 |
| Laki i pastwiska | 0.972 / 80 | +0.06 | -5.0 |

## Wedlug uprawy i kategorii gleby (wszystkie budzety)

| Uprawa | Gleba (TAW) | FAO: plon / woda | Delta plonu RL-FAO [pkt proc.] | Delta wody RL-FAO [mm] |
|---|---|---|---|---|
| Kukurydza na ziarno | 1 (110 mm) | 0.938 / 104 | +0.45 | -6.0 |
| Kukurydza na ziarno | 2 (150 mm) | 0.964 / 88 | +0.32 | -2.0 |
| Kukurydza na ziarno | 3 (185 mm) | 0.979 / 75 | +0.29 | -0.0 |
| Kukurydza na ziarno | 4 (220 mm) | 0.987 / 61 | +0.28 | +4.7 |
| Ziemniak (sredniopozny) | 1 (110 mm) | 0.924 / 119 | +0.61 | -9.8 |
| Ziemniak (sredniopozny) | 2 (150 mm) | 0.951 / 108 | +0.32 | -6.5 |
| Ziemniak (sredniopozny) | 3 (185 mm) | 0.965 / 100 | +0.26 | -5.2 |
| Ziemniak (sredniopozny) | 4 (220 mm) | 0.975 / 91 | +0.26 | -3.6 |
| Pszenica ozima (od wznowienia wegetacji) | 1 (110 mm) | 0.957 / 98 | -0.16 | -12.2 |
| Pszenica ozima (od wznowienia wegetacji) | 2 (150 mm) | 0.976 / 86 | +0.02 | -12.9 |
| Pszenica ozima (od wznowienia wegetacji) | 3 (185 mm) | 0.986 / 75 | +0.13 | -12.2 |
| Pszenica ozima (od wznowienia wegetacji) | 4 (220 mm) | 0.991 / 63 | +0.22 | -10.9 |
| Burak cukrowy | 1 (110 mm) | 0.957 / 100 | +0.20 | -7.9 |
| Burak cukrowy | 2 (150 mm) | 0.975 / 84 | +0.16 | -5.2 |
| Burak cukrowy | 3 (185 mm) | 0.985 / 69 | +0.18 | -1.4 |
| Burak cukrowy | 4 (220 mm) | 0.992 / 56 | +0.18 | +1.1 |
| Zboza jare (jeczmien, pszenica jara) | 1 (110 mm) | 0.949 / 99 | +0.06 | -10.8 |
| Zboza jare (jeczmien, pszenica jara) | 2 (150 mm) | 0.971 / 88 | +0.24 | -11.4 |
| Zboza jare (jeczmien, pszenica jara) | 3 (185 mm) | 0.983 / 75 | +0.30 | -9.3 |
| Zboza jare (jeczmien, pszenica jara) | 4 (220 mm) | 0.989 / 64 | +0.37 | -9.3 |
| Kukurydza na kiszonke | 1 (110 mm) | 0.941 / 102 | +0.41 | -6.8 |
| Kukurydza na kiszonke | 2 (150 mm) | 0.967 / 86 | +0.29 | -4.1 |
| Kukurydza na kiszonke | 3 (185 mm) | 0.980 / 73 | +0.29 | -1.9 |
| Kukurydza na kiszonke | 4 (220 mm) | 0.989 / 58 | +0.28 | +1.6 |
| Rzepak ozimy (od wznowienia wegetacji) | 1 (110 mm) | 0.966 / 91 | -0.63 | -17.5 |
| Rzepak ozimy (od wznowienia wegetacji) | 2 (150 mm) | 0.983 / 74 | -0.33 | -16.1 |
| Rzepak ozimy (od wznowienia wegetacji) | 3 (185 mm) | 0.991 / 62 | -0.14 | -17.9 |
| Rzepak ozimy (od wznowienia wegetacji) | 4 (220 mm) | 0.995 / 47 | -0.04 | -16.4 |
| Rosliny straczkowe (groch, bobik na nasiona) | 1 (110 mm) | 0.957 / 99 | +0.05 | -12.1 |
| Rosliny straczkowe (groch, bobik na nasiona) | 2 (150 mm) | 0.975 / 89 | +0.19 | -12.3 |
| Rosliny straczkowe (groch, bobik na nasiona) | 3 (185 mm) | 0.984 / 80 | +0.26 | -12.2 |
| Rosliny straczkowe (groch, bobik na nasiona) | 4 (220 mm) | 0.989 / 71 | +0.28 | -11.3 |
| Warzywa gruntowe (kapustne) | 1 (110 mm) | 0.970 / 93 | +0.16 | -11.5 |
| Warzywa gruntowe (kapustne) | 2 (150 mm) | 0.984 / 75 | +0.09 | -6.4 |
| Warzywa gruntowe (kapustne) | 3 (185 mm) | 0.990 / 63 | +0.08 | -5.0 |
| Warzywa gruntowe (kapustne) | 4 (220 mm) | 0.994 / 51 | +0.12 | -1.8 |
| Chmiel | 1 (110 mm) | 0.969 / 94 | -0.15 | -13.9 |
| Chmiel | 2 (150 mm) | 0.983 / 78 | -0.08 | -12.1 |
| Chmiel | 3 (185 mm) | 0.990 / 66 | +0.02 | -11.3 |
| Chmiel | 4 (220 mm) | 0.994 / 51 | +0.05 | -8.2 |
| Tyton | 1 (110 mm) | 0.977 / 87 | -0.15 | -16.4 |
| Tyton | 2 (150 mm) | 0.989 / 67 | -0.08 | -12.5 |
| Tyton | 3 (185 mm) | 0.994 / 53 | +0.01 | -10.3 |
| Tyton | 4 (220 mm) | 0.997 / 40 | +0.02 | -8.3 |
| Drzewa owocowe (sad jabloniowy) | 1 (110 mm) | 0.895 / 125 | +0.32 | -3.5 |
| Drzewa owocowe (sad jabloniowy) | 2 (150 mm) | 0.924 / 116 | +0.18 | -1.8 |
| Drzewa owocowe (sad jabloniowy) | 3 (185 mm) | 0.942 / 106 | +0.19 | +1.4 |
| Drzewa owocowe (sad jabloniowy) | 4 (220 mm) | 0.957 / 99 | +0.20 | +2.7 |
| Krzewy owocowe (porzeczka, malina) | 1 (110 mm) | 0.955 / 103 | +0.16 | -8.4 |
| Krzewy owocowe (porzeczka, malina) | 2 (150 mm) | 0.973 / 89 | +0.10 | -6.8 |
| Krzewy owocowe (porzeczka, malina) | 3 (185 mm) | 0.983 / 75 | +0.13 | -3.3 |
| Krzewy owocowe (porzeczka, malina) | 4 (220 mm) | 0.989 / 64 | +0.15 | -2.4 |
| Truskawki | 1 (110 mm) | 0.969 / 110 | +0.35 | -25.5 |
| Truskawki | 2 (150 mm) | 0.984 / 96 | -0.07 | -23.3 |
| Truskawki | 3 (185 mm) | 0.990 / 86 | -0.11 | -22.4 |
| Truskawki | 4 (220 mm) | 0.993 / 75 | -0.08 | -18.9 |
| Laki i pastwiska | 1 (110 mm) | 0.950 / 102 | -0.02 | -8.4 |
| Laki i pastwiska | 2 (150 mm) | 0.969 / 87 | +0.05 | -6.7 |
| Laki i pastwiska | 3 (185 mm) | 0.981 / 73 | +0.08 | -3.8 |
| Laki i pastwiska | 4 (220 mm) | 0.988 / 58 | +0.11 | -0.9 |

## Przykladowe sezony: agent RL vs regula FAO

![Sezony](figures/example_seasons.png)

Gorny panel: niedobor wody w strefie korzeniowej (% TAW, wyzej = wilgotniej) i prog stresu RAW. Srodkowy: opad (szary) i dawki obu strategii. Dolny: woda skumulowana z plonem koncowym.

## Zalozenia ewaluacji

- Wszystkie strategie sa liczone tym samym bilansem FAO-56 i ta sama funkcja plonu FAO-33, na tych samych danych pogodowych i prognozach.
- Uklad czynnikowy: kazdy punkt w kazdej kategorii gleby IUNG-PIB, zeby porownac strategie na wszystkich glebach.
- Parametry upraw z FAO-56/33 (`config/crops.yaml`), pojemnosc wodna gleby z kategorii IUNG-PIB (`config/soils.yaml`).

