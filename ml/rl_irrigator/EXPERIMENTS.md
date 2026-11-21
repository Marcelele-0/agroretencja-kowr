# EXPERIMENTS: dziennik zmian nagrody, szumu i hiperparametrów

Każdy wpis zawiera: co zmieniono, dlaczego i wynik walidacji. Walidacja to rok 2024 z prawdziwymi archiwalnymi prognozami ECMWF; lata 2025–2026 są testowe i nie służą do strojenia.

---

## E0: Kalibracja szumu prognozy (21.11.2026)

**Co:** zamiast szumu z promptu (lognormal σ = 0,4, 15% przegapionych deszczy, 10% fałszywych alarmów, bez zależności od wyprzedzenia) wprowadziłem szum zależny od wyprzedzenia 1–7 dni. Jest skalibrowany na prawdziwych prognozach ECMWF IFS 0,25° (Open-Meteo Previous Runs API) dla roku walidacyjnego 2024: 50 punktów, kwiecień–wrzesień, 9150 par prognoza–obserwacja.

**Dlaczego:** prawdziwe prognozy są dużo gorsze od szumu σ = 0,4. Agent trenowany na zbyt dobrych prognozach ufałby im za bardzo, czyli „jasnowidz” w łagodniejszej wersji.

| Statystyka (wyprzedzenie 1 → 7 dni) | ECMWF 2024 | Szum z promptu | Szum skalibrowany |
|---|---|---|---|
| Przegapiony deszcz, P(F < 0,2·P \| P > 1 mm) | 0,15 → 0,39 | 0,15 → 0,15 | 0,17 → 0,43 |
| σ log(F/P) dla trafień | 0,78 → 0,98 | 0,39 → 0,40 | 0,76 → 0,93 |
| Mediana F/P | 0,90 → 0,85 | 1,00 | 0,92 → 0,95 |
| Fałszywy alarm, P(F > 1 \| P ≤ 1) | 0,10 → 0,28 | 0,10 → 0,11 | 0,10 → 0,28 |
| Korelacja F z P | 0,71 → 0,26 | 0,80 → 0,80 | 0,62 → 0,40 |

Parametry są w `config/rl.yaml` (`forecast_noise`), a pełne liczby w `reports/forecast_calibration.json`. Skalibrowany szum jest nieco pesymistyczny przy wyprzedzeniu 1 dnia i nieco optymistyczny przy 6–7 dniach. Nie stroiłem go dalej, bo w walidacji i teście agent dostaje **prawdziwe** prognozy ECMWF.

---

## Sanity check strategii bazowych (lata treningowe, przed treningiem RL)

`scripts/sanity_baselines.py`: 50 punktów × 8 lat × 4 uprawy, gleba kat. II (TAW 150 mm), budżet 150 mm, szum E0.

| Strategia | Plon względny | Woda [mm] | Perkolacja [mm] | Dni stresu |
|---|---|---|---|---|
| Bez nawadniania | 0,813 | 0 | 57 | 51 |
| Kalendarz | 0,959 | 150 | 123 | 20 |
| Reguła FAO | 0,978 | 104 | 64 | 13 |
| Reguła FAO + prognoza | 0,978 | 101 | 62 | 14 |
| Wyrocznia (64 sezony kukurydzy) | 0,998 | 50 | – | – |
| FAO na tych samych 64 sezonach | 0,996 | 64 | – | – |

Lata suche (2018: 0,745; 2019: 0,694 bez nawadniania) wyraźnie odróżniają się od mokrych (2017: 0,913), więc symulator reaguje poprawnie. Reguła FAO jest bardzo blisko optimum pod względem plonu. Pole do poprawy jest głównie w wodzie: wyrocznia przy idealnej wiedzy zużywa ok. 20% mniej wody przy tym samym plonie.

---

## Wspólne ustawienia walidacji

Walidacja obejmuje rok 2024: 50 punktów × 4 uprawy × 4 kategorie gleby × budżety {50, 100, 150, 250} mm, czyli 3200 sezonów z **prawdziwymi** archiwalnymi prognozami ECMWF. Najlepszy checkpoint wybieram po średniej nagrodzie walidacyjnej (`kowr_rl/train.py`, deterministyczny callback zamiast `EvalCallback`). Nagrody z różnymi c_w nie są porównywalne między przebiegami, więc porównuję plon i wodę względem reguły FAO.

Punkt odniesienia na walidacji: **reguła FAO ma plon 0,9333 przy 107,3 mm** (perkolacja 32,2 mm), FAO + prognoza 0,9323 przy 104,5 mm, kalendarz 0,9150 przy 137,5 mm, bez nawadniania 0,7682.

## E1: konfiguracja z promptu (seed 1, 3 mln kroków)

**Co:** MaskablePPO, 16 środowisk, sieć [128, 128], gamma 0,995, gae_lambda 0,95, n_steps 512, batch 4096, lr 3e-4, ent_coef 0,01, nagroda c_w = 2e-4, c_p = 1e-4. Trening trwał 442 s (6,8 tys. kroków/s).

**Wynik walidacji (najlepszy checkpoint, 2,2 mln kroków):** plon 0,9351 (+0,18 pkt proc. względem FAO) przy 114,5 mm (+7,2 mm), perkolacja 35,3 mm. Nagroda −0,0994 wobec −0,0997 dla FAO, czyli praktycznie remis. Agent **nie spełnia** kryterium „mniej wody”.

**Oględziny sezonów** (`scripts/inspect_run.py`, `reports/figures/inspect_E1_s1.png`): agent podaje często małe dawki 10 mm i trzyma glebę wilgotną. 54% jego wody trafia na glebę, której niedobór nie przekracza połowy RAW. Przy taniej wodzie to racjonalna asekuracja przed stresem, ale przy małym budżecie (50 mm) agent potrafi wydać go wcześnie, a ulewa zamienia część tej wody w perkolację. Najgorszy przypadek walidacyjny: kukurydza, kat. I, 2024, plon 0,815 wobec 0,917 dla FAO. Nie widzę exploitu symulatora (np. podlewania ponad pojemność), tylko politykę zbyt hojną dla wody.

## E2: droższa woda, c_w = 5e-4 (przerwany przy ok. 1 mln kroków)

**Dlaczego:** przy wyższej cenie wody agent powinien rzadziej podlewać „na zapas”.
**Wynik przy 1 mln kroków:** plon 0,9348 przy 123,3 mm. Trajektoria była prawie taka sama jak w E1 i E3, więc przerwałem go, żeby zwolnić procesor dla E4. Sam wyższy koszt wody nie przyspiesza oduczania nadmiernego podlewania.

## E3: woda i perkolacja droższe, c_w = 3,5e-4, c_p = 3e-4 (kontrola dla E4)

**Dlaczego:** perkolacja to główny mechanizm marnowania wody przy podlewaniu przed deszczem.
**Wynik:** w toku (przy 1 mln kroków plon 0,9354, woda 122,2 mm).

## E4: start od reguły FAO (behavior cloning) + PPO, nagroda jak w E3

**Co:** przed PPO uczę aktora naśladować regułę FAO (entropia krzyżowa z maską akcji), a krytyka przewidywać zwroty FAO (MSE). Dane to 8000 sezonów treningowych losowanych jak w środowisku, z syntetyczną prognozą. Statystyki `VecNormalize` inicjuję na tych samych danych. Potem PPO z lr 1e-4 i ent_coef 0,001, żeby nie rozmyć polityki startowej.
**Dlaczego:** E1–E3 pokazują, że PPO od zera bardzo powoli oducza się nadmiernego podlewania. Start od FAO daje politykę bliską reguły, a PPO ma szukać poprawek, np. pomijania dawek przed prognozowanym deszczem.
**Wynik:** w toku.
