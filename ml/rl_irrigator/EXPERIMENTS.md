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

**Trzy ziarna, walidacja 2024 (najlepszy checkpoint każdego):**

| Przebieg | Krok | Plon (FAO 0,9333) | Woda [mm] (FAO 107,3) | Perkolacja [mm] |
|---|---|---|---|---|
| E5_s1 | 3,0 mln | 0,9317 | 99,6 | 30,8 |
| E5_s2 | 2,3 mln | 0,9327 | 101,3 | 31,6 |
| E5_s3 | 1,9 mln | 0,9317 | 97,7 | 31,3 |

Wyniki ziaren są spójne: −0,06 do −0,16 pkt proc. plonu i −6 do −10 mm wody. Do wdrożenia trafia **E5_s3** (najlepsza nagroda walidacyjna). Każdy trening trwał ok. 13,5 min przy 3,7 tys. kroków/s (dwa przebiegi równolegle).

---

## TEST 2025–2026: jedno uruchomienie po wyborze E5 (bez dalszego strojenia)

`uv run python -m kowr_rl.evaluate --runs E5_s1 E5_s2 E5_s3`: 6400 sezonów na strategię, prawdziwe prognozy ECMWF, bootstrap 2000×. Pełny raport: `reports/evaluation.md`.

| | Plon | Woda [mm] |
|---|---|---|
| Reguła FAO | 0,969 | 86 |
| FAO + prognoza | 0,969 | 83 |
| **Agent RL (E5_s3)** | **0,970** | **80** |
| Wyrocznia | 0,973 | 75 |

Różnica RL − FAO: łącznie +0,09 pkt proc. [+0,06; +0,12] i −5,9 mm [−7,7; −4,2]. Według budżetów: 100 mm +0,17 / −2,3; 150 mm +0,25 / −6,7; 250 mm −0,08 / −14,7. **50 mm: +0,04 [−0,03; +0,09] / +0,1 [−0,3; +0,6], czyli remis.** Ziarna: woda 78,0 / 81,6 / 80,2 mm, plon 0,9693 / 0,9695 / 0,9699.

**Werdykt wg kryterium ustalonego przed testem** (istotna wygrana przy każdym budżecie): **niegotowy**, bo przy 50 mm jest remis. Domyślna zostaje reguła FAO, a agent jest eksperymentalny (`policy="rl"`). Według dosłownego kryterium z promptu (same średnie) agent przeszedłby, ale nie zmieniam reguły po zobaczeniu wyniku. Decyzję o przełączeniu `default_policy: rl` zostawiam zespołowi lub mentorowi.

---

## E6: doszlifowanie agenta, cel „pokonać FAO przy każdym budżecie” (21.11.2026, prośba użytkownika)

**Punkt wyjścia z walidacji 2024, nie z testu.** E5_s3 przy 50 mm: ziemniak na glebach 1–2 −0,23 pkt proc., kukurydza na glebie 1 −0,13. Przy 250 mm −0,60 pkt proc. za −24,6 mm. Diagnoza na sezonach walidacyjnych (ziemniak, gleba 1, 50 mm): wyrocznia podaje 4–5 dawek po ok. 10 mm, a FAO i E5 2 dawki po 21–25 mm. Duże dawki uzupełniają glebę do pełna i następny deszcz ucieka w perkolację (wyrocznia 16,7 mm, FAO 20,6 mm, E5 21,7 mm).

Protokół wyboru i testu został zapisany w `PLAN.md` (sekcja 5) **przed** jakąkolwiek ewaluacją E6 na latach 2025–2026. Kandydatów porównuje `scripts/compare_val.py`: różnice wobec FAO na walidacji 2024 przy każdym budżecie, z 95% CI po punktach, i nagroda przy wspólnym `c_w` = 5e-4, `c_p` = 3e-4.

| Wariant | Co zmienione względem E5 | Walidacja: nagroda (FAO −0,1383) | ΔY [pkt proc.] / ΔW [mm] przy 50 / 100 / 150 / 250 mm |
|---|---|---|---|
| E5_s3 | – | −0,1350 | −0,02/−0,8 · +0,00/−3,4 · −0,01/−9,3 · **−0,60**/−24,6 |
| E6a_s1 | obserwacja v2 (+ prognoza na dzień 1 i 2, ETc do końca sezonu, budżet/ETc) + 30% epizodów z budżetem 20–100 mm | −0,1339 | +0,03/−0,9 · +0,06/−3,0 · +0,12/−7,0 · −0,01/−16,9 |
| DAgger (imitacja akcji wyroczni) | start od wyroczni zamiast FAO | – | **nieudany:** student przestał podlewać (trafia 0,6% dni podlewania wyroczni), bo przy jego wiedzy „podlać” rzadko ma > 50% |
| DAgger/AggreVaTe (żal wyroczni) | student minimalizuje oczekiwany żal Q* wyroczni | −0,1361 po starcie | **nieudany:** podlewa za mało (plon 0,927, 91 mm). Wyrocznia „wie”, że jutro poda wodę idealnie, więc czekanie wydaje się tanie |
| `fao_small` (prosta reguła, nie RL) | 10 mm przy Dr > 0,9 RAW, pominięcie przy prognozie ≥ 5 mm na 2 dni | −0,1352 | +0,06/−0,3 · +0,13/−1,3 · +0,16/−3,5 · +0,07/−7,2 (**dominuje FAO**) |
| E6d_s1 | E6a + start od `fao_small`, lr 1e-4 | −0,1341 | −0,01/−1,5 · −0,08/−4,9 · −0,18/−11,5 · −0,17/−21,3 |
| E6e_s1 | E6a + start od `fao_small`, lr 3e-4 | −0,1328 | +0,05/−1,4 · +0,08/−4,3 · +0,04/−10,4 · −0,11/−21,9 |
| **E6f_s2@dom** | E6e + checkpoint „dominujący” (najlepsza nagroda wśród checkpointów z ΔY ≥ 0 i ΔW ≤ 0 przy każdym budżecie) | **−0,1328** | **+0,07/−0,9 · +0,11/−3,4 · +0,13/−8,5 · +0,14/−17,1** |
| E6f_s1@dom / s3@dom | jw., inne ziarna | −0,1344 / −0,1350 | wszystkie ΔY > 0, wszystkie ΔW < 0 |
| ensemble E6f s1+s2+s3 @dom | średnia prawdopodobieństw akcji | −0,1342 | dominuje, ale gorzej niż najlepsze ziarno |
| E6g_s1@dom | E6f z `c_w` = 3e-4 i lr malejącym liniowo | −0,1337 | +0,06/−0,6 · +0,13/−2,2 · +0,19/−5,5 · +0,28/−10,9 |

Siatka parametrów `fao_small` na walidacji (próg 0,8–0,95 RAW, pominięcie przy 3/5/8 mm, horyzont 1–3 dni, dawka 10/20 mm) dała płaskie plateau (nagroda −0,1347 do −0,1352). Zostawiłem wariant sprawdzony jako pierwszy, sprzed siatki, żeby nie dopasowywać reguły pod walidację.

**Co zadziałało:**
1. Nowe cechy plus więcej trudnych przypadków w treningu (E6a).
2. Start od reguły małych dawek zamiast od FAO (E6e).
3. Wybór checkpointu, który na walidacji dominuje FAO przy każdym budżecie.

Przy cenie wody `c_w` = 5e-4 PPO z czasem zaczyna przy dużych budżetach wymieniać plon na wodę, a ziarna mocno się różnią: przy 250 mm s2 i s3 dochodziły w trakcie treningu do −1,4 i −0,9 pkt proc. Wybór checkpointu „@dom” to stabilizuje.
**Wybór (przed testem, `PLAN.md`):** E6f_s2@dom, checkpoint 2,8 mln kroków, trening 676 s. Ensemble odpadł, bo miał niższą nagrodę.

### TEST E6: czysty test na nowych lokalizacjach (holdout) + powtórka starego testu

`uv run python -m kowr_rl.evaluate --runs E6f_s1@dom E6f_s2@dom E6f_s3@dom --dataset holdout`

Zestaw holdout ma 50 nowych punktów rolnych (16 województw). To siatka przesunięta o 0,35°, a najbliższy punkt treningowy jest co najmniej 35 km dalej (mediana 43 km). Lata 2025–2026, prawdziwe prognozy ECMWF, 6400 sezonów na strategię. Pełny raport: `reports/evaluation_holdout.md`.

| Holdout 2025–2026 | Plon | Woda [mm] | Perkolacja [mm] |
|---|---|---|---|
| Reguła FAO | 0,967 | 87 | 41 |
| FAO + prognoza | 0,967 | 84 | 40 |
| FAO małe dawki + prognoza | 0,969 | 83 | 38 |
| **Agent RL (E6f_s2@dom)** | **0,970** | **81** | 38 |
| Wyrocznia | 0,972 | 76 | 37 |

| Zakres | ΔPlon RL − FAO [pkt proc.] | ΔWoda RL − FAO [mm] | Wynik |
|---|---|---|---|
| łącznie | +0,23 [+0,14; +0,33] | −6,0 [−7,3; −5,1] | OK |
| 50 mm | +0,12 [+0,07; +0,17] | −0,9 [−1,4; −0,5] | OK |
| 100 mm | +0,26 [+0,12; +0,43] | −3,9 [−5,2; −2,8] | OK |
| 150 mm | +0,28 [+0,13; +0,44] | −8,2 [−10,0; −6,5] | OK |
| 250 mm | +0,27 [+0,20; +0,33] | −11,2 [−13,2; −9,6] | OK |

Przy **każdym** budżecie agent ma istotnie wyższy plon **i** istotnie mniejsze zużycie wody, więc spełnia obie ścieżki kryterium naraz.

- **RL wobec `fao_small`** (najlepsza prosta reguła): łącznie +0,11 pkt proc. i −2,0 mm, istotnie przy każdym budżecie.
- **RL wobec FAO + prognoza:** +0,27 pkt proc. i −2,7 mm.
- **Ziarna na holdoucie:** plon 0,9688 / 0,9697 / 0,9686, woda 81,5 / 80,9 / 82,9 mm. Wynik nie zależy od szczęśliwego ziarna.
- **Wyrocznia:** agent zbiera ok. 55–60% jej przewagi nad FAO, jednocześnie w plonie i w wodzie.

**Powtórka starego testu** (punkty treningowe 2025–2026, już widziane przy E5): łącznie +0,23 pkt proc. [+0,14; +0,34] i −5,6 mm [−6,8; −4,7]. Przy 50 mm +0,11 / −0,8, przy 100 mm +0,25 / −3,8, przy 150 mm +0,28 / −7,8, przy 250 mm +0,28 / −10,2. Wszystko istotne (`reports/evaluation.md`). Archiwum testu E5: `reports/evaluation_E5.md`.

**Słabe miejsce:** pszenica ozima na glebie kat. I. Na holdoucie −0,19 pkt proc. plonu za −11,8 mm wody, a 9% takich sezonów traci ponad 1 pkt proc. Najgorszy sezon to −3,2 pkt proc., kiedy agent zostawił 30 mm budżetu niewykorzystane pod koniec sezonu. W pozostałych 15 kombinacjach uprawy i gleby średni plon jest wyższy niż przy FAO.

**Werdykt (reguła z M5 bez zmian, na czystym teście):** agent **GOTOWY**, więc `default_policy: rl`. Reguły FAO, FAO + prognoza i `fao_small` pozostają dostępne jako `policy=...`.
**Zastrzeżenia:**
- Lata 2025–2026 były już raz oglądane przy E5, a pomysł na E6 wziął się z diagnozy, którą widać też na walidacji. Holdout jest niezależny przestrzennie, ale nie czasowo.
- Wdrożone ziarno to najlepsze z trzech na walidacji.
- Wszystko to wyniki modelu FAO-56/33, nie pomiary polowe.
