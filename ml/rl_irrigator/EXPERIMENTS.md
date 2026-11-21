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
