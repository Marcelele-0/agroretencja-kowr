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

## E3: woda i perkolacja droższe, c_w = 3,5e-4, c_p = 3e-4 (kontrola dla E4, przerwany przy 1,6 mln kroków)

**Dlaczego:** perkolacja to główny mechanizm marnowania wody przy podlewaniu przed deszczem.
**Wynik:** najlepszy checkpoint (1,4 mln kroków) ma plon 0,9354 przy 118,0 mm (+10,7 mm względem FAO). Nagroda walidacyjna była gorsza niż sama polityka po BC w E4, czyli PPO od zera przegrywa z klonem FAO nawet po 1,4 mln kroków. Przerwałem go, żeby zwolnić procesor.

## E4: start od reguły FAO (behavior cloning) + PPO, nagroda jak w E3

**Co:** przed PPO uczę aktora naśladować regułę FAO (entropia krzyżowa z maską akcji), a krytyka przewidywać zwroty FAO (MSE). Dane to 8000 sezonów treningowych losowanych jak w środowisku, z syntetyczną prognozą. Statystyki `VecNormalize` inicjuję na tych samych danych. Potem PPO z lr 1e-4 i ent_coef 0,001, żeby nie rozmyć polityki startowej.
**Dlaczego:** E1–E3 pokazują, że PPO od zera bardzo powoli oducza się nadmiernego podlewania. Start od FAO daje politykę bliską reguły, a PPO ma szukać poprawek, np. pomijania dawek przed prognozowanym deszczem.
**Wynik BC:** zgodność z FAO wynosi 99,8% decyzji (91–97% w dniach z nawadnianiem). Walidacja zaraz po BC: plon 0,9331 przy 107,2 mm, czyli praktycznie sama reguła FAO.
**Wynik po PPO (najlepszy checkpoint 2,5 mln kroków, trening 850 s):** plon **0,9341 (+0,08 pkt proc.)** przy **103,7 mm (−3,6 mm, −3,4%)**, perkolacja 31,4 mm. Próba ewaluacji na walidacji (bootstrap po punktach) pokazuje, że agent jest lepszy od FAO w obu wymiarach przy każdym budżecie: Δplon od +0,04 do +0,09 pkt proc., Δwoda od −0,7 mm (50 mm) do −7,2 mm (250 mm). Zostaje jako **wariant alternatywny**: odrobinę wyższy plon i mniej wody.

## Ile w ogóle da się zyskać? Wyrocznia na walidacji 2024

Programowanie dynamiczne ze znaną pogodą (3200 sezonów walidacyjnych):

| Nagroda | Wyrocznia: plon / woda | Reguła FAO: plon / woda |
|---|---|---|
| c_w = 2e-4, c_p = 1e-4 | 0,9370 / 100,4 mm | 0,9333 / 107,3 mm |
| c_w = 3,5e-4, c_p = 3e-4 | 0,9364 / 98,3 mm | 0,9333 / 107,3 mm |

**Wniosek:** w tym modelu (jedno wiadro, sezonowe Ky) reguła FAO jest blisko optimum. Nawet idealna wiedza o pogodzie daje najwyżej ok. +0,4 pkt proc. plonu i 6–9% wody. Zysk RL musi się więc mieścić w kilku mm wody na sezon, a tak naprawdę ogranicza go jakość prognoz, nie metoda.

## E5: start od FAO + droższa woda, c_w = 5e-4, c_p = 3e-4, lr 3e-4, ent_coef 0,001 (konfiguracja finalna)

**Dlaczego:** kryterium dopuszcza do 1 pkt proc. niższy plon przy mniejszym zużyciu wody, a celem KOWR jest efektywność wody. Wyższa cena wody (1 pkt proc. plonu ≈ 20 mm) przesuwa agenta w stronę oszczędności. Wyższy lr pozwala szybciej odejść od klonu FAO.
**Wynik (seed 1, najlepszy checkpoint 3,0 mln kroków, trening 824 s):** plon **0,9317 (−0,16 pkt proc.)** przy **99,6 mm (−7,7 mm, −7,2%)**, perkolacja 30,8 mm. To prawie tyle oszczędności wody co wyrocznia. Próba ewaluacji na walidacji przechodzi kryterium przy każdym budżecie: Δplon od −0,07 (50 mm) do −0,29 pkt proc. (250 mm), Δwoda od −1,4 mm do −16,2 mm, cały 95% CI < 0.
**Wybór:** E5 jako konfiguracja finalna, bo oszczędza dwa razy więcej wody niż E4 przy koszcie plonu dużo poniżej limitu. Ziarna 2 i 3 trenowane z identyczną konfiguracją. Lata testowe 2025–2026 zostały użyte dopiero po tym wyborze.
