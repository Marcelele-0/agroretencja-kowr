# Plan nawadniania na 14 dni: research i testy (E8, 21.11.2026)

**Pytanie:** czy agent może planować nawadnianie na 14 dni do przodu, decydując codziennie dla każdego z tych 14 dni na podstawie tych samych danych co dziś, ale z prognozą na kolejne 14 dni (czyli potrzebując pogody zakładanej na 28 dni)?

**Odpowiedź w skrócie:**
- **Plan na 14 dni działa jako plan kroczący.** Codziennie liczy się od nowa, a wiążąca jest dawka na dziś.
- **Konkretne dni podlewania** da się wskazać wiarygodnie na ok. 3 dni, z tolerancją ±1 dzień na ok. 7 dni.
- **Zapotrzebowanie na wodę w 14 dni** jako zakres P10–P90 jest dobrze skalibrowane: obejmuje rzeczywistość w ~90% przypadków.
- **Prognoza na dni 8–14 w decyzji dziennej** nie poprawia wyników: agent E8 z 14-dniowym oknem prognozy wypada tak samo jak agent z 7-dniowym.

---

## 1. Research

### 1.1. Dostępne prognozy

| Źródło | Horyzont | Archiwum | Użycie |
|---|---|---|---|
| Open-Meteo Forecast API, ECMWF IFS 0,25° | 15 dni | – (tylko bieżąca) | plan dla dzisiejszej daty |
| Open-Meteo Previous Runs API, ECMWF IFS 0,25° | wyprzedzenia 1–7 dni | od 01.2024, każdy dzień | trening, walidacja, testy E1–E8 |
| Open-Meteo Single Runs API, ECMWF IFS HRES 9 km | 10 dni do lipca 2024, potem 15 dni (sprawdzone na przebiegach z 2024–2026) | od 14.03.2024, pełne przebiegi | pomiar trafności dni 8–14 i test planu na prawdziwej prognozie 14-dniowej |
| (brak) | dni 16–28 | – | tu zawsze klimatologia albo scenariusze z innych lat |

Plan na 14 dni z 14-dniowym oknem prognozy w każdym dniu potrzebuje pogody zakładanej na 28 dni. Prognoza numeryczna sięga najwyżej 15 dni, więc dni 16–28 wypełniamy pogodą z poprzednich lat.

### 1.2. Literatura

- **Prognozy ECMWF:** przewaga prognozy opadu nad klimatologią maleje z wyprzedzeniem. Dla tygodnia 2 (dni 7–14) ECMWF podaje dobrze oddany wzór opadów w skali dużych regionów, ale z mniejszą amplitudą, a tydzień 3 jest już bliski klimatologii ([ECMWF Newsletter 184](https://www.ecmwf.int/node/29447), [ocena prognoz ECMWF 2025](https://www.ecmwf.int/sites/default/files/elibrary/092025/81680-evaluation-of-ecmwf-forecasts.pdf)).
- **Nawadnianie a horyzont prognozy:**
  - Ajaz i in. (2024) porównali horyzonty 3, 7 i 14 dni dla kukurydzy, bawełny i sorgo. Krótsze horyzonty dokładniej oddają pogodę, dłuższe pomagają przy dużych systemach nawadniania i planowaniu ([PIK](https://publications.pik-potsdam.de/pubman/item/item_28704)).
  - Cai i in. (2011) oszacowali 11–27% oszczędności wody dzięki prognozom na 1–7 dni ([OSTI](https://www.osti.gov/biblio/1029038)).
  - Hu i in. (2025) dla ryżu podają ok. 21% mniej wody przy prognozie na 7 dni ([Frontiers in Agronomy](https://www.frontiersin.org/journals/agronomy/articles/10.3389/fagro.2025.1598811/full)).
- **Wniosek z literatury:** wartość prognozy dla nawadniania skupia się w pierwszym tygodniu, a dłuższe horyzonty przydają się do planowania zasobów wody.

### 1.3. Trafność prawdziwych prognoz ECMWF na naszych punktach (`forecast_skill_by_lead.json`)

Opad dobowy, miesiące IV–IX, ECMWF IFS 0,25° (Previous Runs). Przewaga nad klimatologią to 1 − MAE prognozy / MAE klimatologii; 0 oznacza „nie lepiej niż średnia z innych lat”.

| Wyprzedzenie [dni] | 1 | 2 | 3 | 4 | 5 | 6 | 7 |
|---|---|---|---|---|---|---|---|
| Przewaga nad klimatologią, walidacja 2024 | 0,49 | 0,43 | 0,36 | 0,29 | 0,19 | 0,08 | 0,05 |
| Przewaga nad klimatologią, holdout 2025–2026 | 0,54 | 0,44 | 0,36 | 0,27 | 0,19 | 0,12 | 0,05 |
| Trafienia opadu ≥ 5 mm, holdout | 66% | 59% | 53% | 47% | 38% | 32% | 28% |

Przewaga maleje mniej więcej liniowo, o ~0,07 na dzień, i dochodzi do zera przy ok. 8 dniach. Pomiar dni 8–14 na archiwalnych przebiegach HRES jest przygotowany (sekcja 4).

---

## 2. Model

- **Plan** (`kowr_rl/plan.py`, `recommend(..., plan_days=14)`, API `plan_days`):
  - ta sama polityka decyduje dzień po dniu na pogodzie zakładanej dziś;
  - w dniu planu k widzi okno prognozy na dni k..k+13, czyli 28 dni pogody;
  - tam, gdzie jest prognoza, bierze prognozę, dalej klimatologię dnia roku;
  - stan gleby w planie zmienia się zgodnie z tą zakładaną pogodą.
- **Scenariusze:** pogoda tych samych dni w 10 innych latach archiwum (po prognozie) daje 10 wariantów planu. Z nich liczymy prawdopodobieństwo podlewania w każdym dniu oraz zakres wody na 14 dni (P10–P90).
- **Agent E8:**
  - obserwacja v3, czyli dodatkowa cecha „suma prognozy na dni 8–14”;
  - prognoza treningowa ma 14 dni (szum ekstrapolowany z kalibracji dni 1–7);
  - konfiguracja identyczna z E7 (15 upraw, 6 mln kroków, 3 ziarna), więc różnica E8 − E7 mierzy tylko wartość okna 8–14.

---

## 3. Testy

Planowanie co 7 dni w każdym sezonie. Porównuję plan z tym, co agent faktycznie zrobi, decydując codziennie z nową prognozą. Budżety 100 i 250 mm, 15 upraw, 4 gleby.

### 3.1. Trafność dni podlewania (agent RL, holdout 2025–2026, 241 600 planów)

| Dzień planu | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 10 | 14 |
|---|---|---|---|---|---|---|---|---|---|---|
| Trafione dni podlewania (recall) | 100% | 62% | 41% | 34% | 34% | 23% | 9% | 6% | 9% | 11% |
| Zaplanowane podlewanie trafia w rzeczywiste ±1 dzień | 100% | 87% | 83% | 79% | 74% | 66% | 60% | 53% | 52% | 39% |
| Przewaga prawdopodobieństwa podlewania nad częstością bazową (Brier skill) | – | 0,43 | 0,27 | 0,17 | 0,15 | 0,12 | 0,10 | 0,08 | 0,09 | 0,05 |

Plan bez prognozy (sama klimatologia) trafia 8–11% dni podlewania w każdym dniu.
Na walidacji 2024, roku z trudniejszą prognozą, wyniki są podobne, ale słabsze: przewaga dnia 2 to 0,30, a od dnia 4 jest bliska zera.

### 3.2. Zapotrzebowanie na wodę w 14 dni

| | Walidacja 2024 | Holdout 2025–2026 |
|---|---|---|
| Rzeczywiste zużycie przy codziennych decyzjach [mm/14 dni] | 10,7 | 8,3 |
| Średnia ze scenariuszy [mm] | 8,8 | 7,5 |
| Średni błąd bezwzględny [mm] | 5,4 | 4,6 |
| Przedział P10–P90 obejmuje rzeczywistość | 88% | 92% |

### 3.3. Plan wykonany bez aktualizacji (holdout, średnio na 14 dni)

| | Niedobór wody dla roślin (ETc − ETa) [mm] | Perkolacja [mm] | Woda [mm] |
|---|---|---|---|
| Codzienne decyzje (plan kroczący) | **0,75** | 2,98 | 8,3 |
| Plan z prognozą wykonany 14 dni bez zmian | 1,29 | 2,99 | 4,7 |
| Plan z samą klimatologią | 1,85 | 3,01 | 3,3 |

Średnia klimatologia to codziennie trochę deszczu. W takiej zakładanej pogodzie gleba wolniej przesycha i sztywny plan zakłada za mało wody. Scenariusze z prawdziwych lat (3.2) nie mają tej wady, dlatego to one podają zakres zapotrzebowania.

### 3.4. Agent z 14-dniowym oknem prognozy (E8) a agent 7-dniowy (E7)

Walidacja 2024, 15 upraw, nagroda wspólna dla obu agentów (FAO −0,1291):

| | Prawdziwa prognoza (dni 1–7, dalej klimatologia) | Prognoza syntetyczna z informacją także na dni 8–14 |
|---|---|---|
| E7, średnia z 3 ziaren (najlepsze checkpointy) | −0,1243 | −0,1244 |
| E8, średnia z 3 ziaren (najlepsze checkpointy) | −0,1241 | −0,1240 |

E8 korzysta z prognozy na dni 8–14: jej wymieszanie zmienia 13–17% jego decyzji o podlewaniu. Wynik jest jednak taki sam jak bez niej, a różnica jest kilkakrotnie mniejsza niż rozrzut między ziarnami. Dzisiejsza dawka 10 mm wpływa na glebę tylko przez kilka dni, a o tym, czy podlać dziś, decyduje opad w ciągu 1–3 dni. Wdrożonym agentem zostaje E6f_s2.

---

## 4. Wnioski i dalsze kroki

1. **Plan na 14 dni ma sens jako plan kroczący:** dawka na dziś, wskazówki na dni 2–3, prawdopodobieństwo podlewania na kolejne dni i zakres zapotrzebowania na wodę na 14 dni. Taki plan jest w API: `plan_days=14` → `recommendation.plan_14d` z pewnością każdego dnia (wysoka: dni 1–3, średnia: 4–7, niska: 8–14).
2. **Zapotrzebowanie na wodę w 14 dni** ze scenariuszy jest dobrze skalibrowane i nadaje się do planowania zasobów (zbiornik, pompa, limit poboru).
3. **Dłuższe okno prognozy w decyzji dziennej** (E8) nie zmienia wyniku, więc agent 7-dniowy wystarcza.
4. **Pomiar na prawdziwych prognozach 14-dniowych** (ECMWF HRES, Single Runs API) jest przygotowany. 21.11.2026 skończył się dzienny limit darmowego Open-Meteo dla tego adresu IP. Po jego odnowieniu:
   ```bash
   uv run --group train python ml/rl_irrigator/scripts/fetch_single_runs.py        # ~1900 przebiegów, ok. 20-30 min
   uv run --group train python ml/rl_irrigator/scripts/forecast_skill.py           # trafność dni 1-14
   uv run --group train python ml/rl_irrigator/scripts/plan_study.py --dataset holdout --forecast hres14
   ```

Pliki: `reports/forecast_skill_by_lead.json`, `reports/plan14_train_points_prev7.json`, `reports/plan14_holdout_prev7.json`, `reports/e8_val_real.json`, `reports/e8_val_synthetic.json`.
