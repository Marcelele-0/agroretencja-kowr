# FAO-RL-Irrigator: jak agent decyduje o nawadnianiu (krok 4 KOWR)

> **Moduł:** [`ml/rl_irrigator`](../ml/rl_irrigator/README.md) · **Wdrożony agent:** `E6f_s2@dom` ([`artifacts/models/ppo_irrigator_weights.npz`](../artifacts/models/ppo_irrigator_weights.npz)) · **Wyniki:** [`reports/evaluation_holdout.md`](../ml/rl_irrigator/reports/evaluation_holdout.md)

Każda decyzja agenta opiera się na dwóch warstwach:

1. **Model fizyczny:** dobowy bilans wody w strefie korzeniowej (FAO-56) i odpowiedź plonu na niedobór wody (FAO-33). Ta warstwa liczy, ile wody jest w glebie i jakie skutki ma każda dawka.
2. **Sieć decyzyjna:** mała sieć neuronowa (MaskablePPO). Co rano dostaje 17 liczb opisujących glebę, pogodę, prognozę, uprawę i budżet i wybiera dawkę 0, 10, 20 albo 30 mm.

Poniżej obie warstwy z pełną matematyką. Część 5 to pomiar na wdrożonym modelu: na co agent faktycznie reaguje.

---

## 1. Dane wejściowe

| Co | Skąd | Do czego |
|---|---|---|
| Lokalizacja: lat/lon albo numer działki | użytkownik; dla działki środek geometrii z GUGiK ULDK | wszystkie zapytania o dane |
| Uprawa, data, budżet sezonowy $B$, budżet już zużyty | użytkownik | parametry uprawy, dzień sezonu, ile wody zostało |
| Historia nawodnień (opcjonalnie) | użytkownik | odtworzenie dotychczasowego sezonu |
| Kategoria gleby I–IV | IUNG-PIB WMS `Susza:katgl_landuse2024` | pojemność wodna $TAW$ = 110 / 150 / 185 / 220 mm |
| Dobowy opad $P$ i $ET_0$ od 1 marca | Open-Meteo, archiwum | bilans od początku sezonu |
| Prognoza opadu na 1–7 dni | ECMWF IFS 0,25°: bieżąca, archiwalna (od 2024) albo klimatologia punktu | decyzje i plan na 7 dni |
| $K_{c,ini}$, $K_{c,mid}$, $K_{c,end}$, długości faz, $p$, $K_y$ | [`config/crops.yaml`](../ml/rl_irrigator/config/crops.yaml) (FAO-56/33) | zapotrzebowanie uprawy i wrażliwość plonu |

| Uprawa | $K_c$ ini / mid / end | Fazy [dni] | Start sezonu | $p$ | $K_y$ |
|---|---|---|---|---|---|
| Kukurydza na ziarno | 0,30 / 1,20 / 0,60 | 30 / 40 / 50 / 50 | 25 IV | 0,55 | 1,25 |
| Ziemniak | 0,50 / 1,15 / 0,75 | 30 / 35 / 50 / 30 | 20 IV | 0,35 | 1,10 |
| Pszenica ozima (od wznowienia wegetacji) | 0,40 / 1,15 / 0,30 | 20 / 45 / 40 / 20 | 25 III | 0,55 | 1,05 |
| Burak cukrowy | 0,35 / 1,20 / 0,70 | 50 / 40 / 50 / 40 | 10 IV | 0,55 | 1,00 |

---

## 2. Model fizyczny: dobowy bilans wody (FAO-56)

Stan gleby opisuje jedna liczba: **niedobór wody $D_r$ [mm]**, czyli ile brakuje do pojemności polowej. $D_r = 0$ oznacza glebę pełną, a $D_r = TAW$ glebę suchą.

### 2.1. Współczynnik uprawy $K_c(t)$

Zmienia się odcinkami liniowo w czterech fazach (FAO-56, wzór 66):

$$
K_c(i) =
\begin{cases}
K_{c,ini} & \text{faza początkowa} \\
K_{c,ini} + \dfrac{i - i_{dev} + 1}{L_{dev}}\,(K_{c,mid} - K_{c,ini}) & \text{rozwój} \\
K_{c,mid} & \text{środek sezonu} \\
K_{c,mid} + \dfrac{i - i_{late} + 1}{L_{late}}\,(K_{c,end} - K_{c,mid}) & \text{faza późna}
\end{cases}
$$

### 2.2. Jeden dzień bilansu ([`simulator.balance_step`](../ml/rl_irrigator/kowr_rl/simulator.py))

$$
\begin{aligned}
ET_c &= K_c \cdot ET_0 && \text{zapotrzebowanie uprawy [mm/d]}\\
p &= \mathrm{clip}\big(p_{tab} + 0{,}04\,(5 - ET_c),\ 0{,}1,\ 0{,}8\big) && \text{próg wyczerpania (w upale niższy)}\\
RAW &= p \cdot TAW && \text{woda łatwo dostępna}\\
K_s &= \begin{cases} 1 & D_r \le RAW \\ \dfrac{TAW - D_r}{TAW - RAW} & D_r > RAW \end{cases} && \text{współczynnik stresu}\\
ET_a &= \min\big(K_s \cdot ET_c,\ TAW - D_r + P + I\big) && \text{rzeczywiste parowanie}\\
net &= D_r - P - I + ET_a\\
DP &= \max(0,\ -net) && \text{perkolacja w głąb profilu}\\
D_r' &= \max(0,\ net) && \text{niedobór na jutro}
\end{aligned}
$$

Bilans masy zamyka się dokładnie: zmiana zapasu wody = $P + I - ET_a - DP$. Testy sprawdzają to z dokładnością $10^{-9}$ na 200 tys. losowych kroków.

**Przykład:** kukurydza w środku sezonu, gleba II ($TAW$ = 150 mm), $ET_0$ = 4 mm.
- $ET_c = 1{,}2 \cdot 4 = 4{,}8$ mm
- $p = 0{,}55 + 0{,}04 \cdot 0{,}2 = 0{,}558$, więc $RAW = 83{,}7$ mm
- Przy $D_r = 100$ mm: $K_s = 50 / 66{,}3 = 0{,}75$, więc $ET_a = 3{,}6$ mm
- Roślina traci tego dnia 1,2 mm transpiracji.

**Start sezonu:** bilans rusza 1 marca od $D_r = 0$ (gleba pełna po zimie) z $K_c = 0{,}3$ i $p = 0{,}55$. Do dnia siewu liczy się na prawdziwej pogodzie.

### 2.3. Plon (FAO-33)

$$
\frac{Y_a}{Y_m} = \mathrm{clip}\left(1 - K_y\left(1 - \frac{\sum ET_a}{\sum ET_c}\right),\ 0,\ 1\right)
$$

**Przykład** dla kukurydzy ($K_y = 1{,}25$): jeśli przez sezon $\sum ET_a = 405$ mm przy $\sum ET_c = 450$ mm, plon względny wynosi $1 - 1{,}25 \cdot 0{,}1 = 0{,}875$, czyli 12,5% straty.

---

## 3. Co widzi agent: 17 cech obserwacji

Agent dostaje tylko informacje dostępne rano danego dnia: przeszłą pogodę, prognozę, bieżący stan gleby, budżet i parametry. Przyszłej pogody nie zna.

| # | Cecha | Wzór |
|---|---|---|
| 1 | postęp sezonu | $t / L$ |
| 2 | $K_c$ dziś | $K_c(t)$ |
| 3 | wyczerpanie gleby | $D_r / TAW$ |
| 4 | opad z ostatnich 7 dni | $\sum P_{-7..-1} / 50$ mm |
| 5 | $ET_0$ z ostatnich 7 dni | $\sum ET_{0,-7..-1} / 35$ mm |
| 6 | prognoza na 3 dni | $(F_1 + F_2 + F_3) / 50$ mm |
| 7 | prognoza na 7 dni | $\sum_{j=1}^{7} F_j / 50$ mm |
| 8 | część budżetu, która została | $B_{left} / B$ |
| 9 | budżet, który został | $B_{left} / 250$ mm |
| 10 | pojemność gleby | $TAW / 250$ mm |
| 11 | wrażliwość uprawy na wyczerpanie | $p_{tab}$ |
| 12 | wrażliwość plonu | $K_y$ |
| 13 | odległość od progu stresu | $(RAW_{est} - D_r) / TAW$ |
| 14 | prognoza na jutro | $F_1 / 20$ mm |
| 15 | prognoza na pojutrze | $F_2 / 20$ mm |
| 16 | zapotrzebowanie do końca sezonu | $ET_{c,rem} / 400$ mm |
| 17 | budżet wobec tego zapotrzebowania | $\min\big(B_{left} / \max(ET_{c,rem}, 10),\ 3\big)$ |

Rano agent nie zna dzisiejszego $ET_0$, więc próg stresu szacuje z ostatniego tygodnia. Zapotrzebowanie do końca sezonu liczy z przebiegu $K_c$:

$$
RAW_{est} = \mathrm{clip}\left(p_{tab} + 0{,}04\left(5 - K_c \cdot \tfrac{1}{7}\textstyle\sum ET_{0,-7..-1}\right),\ 0{,}1,\ 0{,}8\right) \cdot TAW
\qquad
ET_{c,rem} = ET_{c,sezon}^{est} \cdot \frac{\sum_{k \ge t} K_{c,k}}{\sum_{k} K_{c,k}}
$$

$ET_{c,sezon}^{est}$ to szacunek sezonowego $ET_c$ dla tego punktu:
- **w treningu:** średnia z lat 2016–2023;
- **w rekomendacji:** obserwacje do dziś plus klimatologia punktu na resztę sezonu.

Parametry uprawy i gleby (cechy 10–12) są w obserwacji. Dzięki temu **jedna sieć obsługuje wszystkie uprawy i gleby**.

---

## 4. Sieć decyzyjna: matematyka jednej decyzji

$$
\begin{aligned}
x &= \mathrm{clip}(o,\ -10,\ 10) && \text{17 cech}\\
z &= \mathrm{clip}\!\left(\frac{x - \mu}{\sqrt{\sigma^2 + 10^{-8}}},\ -10,\ 10\right) && \mu, \sigma \text{ z danych treningowych}\\
h_1 &= \tanh(W_1 z + b_1) && W_1 \in \mathbb{R}^{128 \times 17}\\
h_2 &= \tanh(W_2 h_1 + b_2) && W_2 \in \mathbb{R}^{128 \times 128}\\
\ell &= W_3 h_2 + b_3 && W_3 \in \mathbb{R}^{4 \times 128},\ \text{dawki } [0, 10, 20, 30]\ \text{mm}\\
\ell_a &= -\infty \quad \text{gdy dawka } a > B_{left} && \text{maska budżetu}\\
\pi(a) &= \mathrm{softmax}(\ell)_a, \qquad a^* = \arg\max_a \pi(a)
\end{aligned}
$$

- **Rozmiar:** sieć ma 19 332 parametry.
- **Czas:** jedna decyzja to około mikrosekundy w czystym NumPy, bez PyTorcha ([`policy.NumpyPolicy`](../ml/rl_irrigator/kowr_rl/policy.py)).
- **Maska budżetu:** dzięki niej agent nie może przekroczyć budżetu sezonowego.
- **Uzasadnienie w API:** prawdopodobieństwa $\pi(a)$ trafiają do uzasadnienia rekomendacji.

---

## 5. Na co agent reaguje (pomiar na wdrożonym modelu)

### 5.1. Ważność cech

Na 404 tys. stanów z sezonów walidacyjnych 2024 losowo mieszałem po kolei wartości każdej cechy (permutation importance) i liczyłem, ile decyzji o podlewaniu się zmienia:

| Cecha | Zmienione decyzje w dni podlewania |
|---|---|
| odległość od progu stresu $(RAW - D_r)/TAW$ | **76%** |
| wyczerpanie gleby $D_r/TAW$ | **70%** |
| $K_c$ (faza rozwoju) | **58%** |
| prognoza na 3 dni | 33% |
| prognoza na pojutrze | 26% |
| prognoza na jutro | 20% |
| $p$ uprawy | 20% |
| opad z ostatnich 7 dni | 16% |
| budżet w mm | 13% |
| $K_y$ | 12% |
| $TAW$, postęp sezonu, budżet / $ET_c$ do końca | 6–9% |
| prognoza na 7 dni, $ET_0$ z 7 dni | 3–5% |

Kolejność ważności: **stan gleby względem progu stresu** > **faza uprawy** > **prognoza na 1–3 dni** > cechy uprawy i gleby > budżet.

### 5.2. Mapa decyzji

Kukurydza w środku sezonu na glebie II ($TAW$ = 150 mm, $RAW \approx$ 60% $TAW$), zostało 75 z 150 mm budżetu:

| Wyczerpanie $D_r$ | brak deszczu | 3 mm jutro | 8 mm w 2 dni | 15 mm jutro | 20 mm za 4–6 dni |
|---|---|---|---|---|---|
| 30–45% $TAW$ | 0 | 0 | 0 | 0 | 0 |
| 50–60% $TAW$ | **10 mm** | 10 mm | 0 | 0 | 10 mm |
| 70–80% $TAW$ | 10 mm | 10 mm | **10 mm** | 0 | 10 mm |

### 5.3. Strategia, której agent się nauczył

- **Dawki po 10 mm.** Na walidacji 100% dawek agenta to 10 mm. Mała dawka zostawia w glebie miejsce na deszcz, więc mniej wody ucieka w perkolację niż przy uzupełnianiu gleby do pełna dawkami 25–30 mm, jak w regule FAO.
- **Podlewa tuż przed progiem stresu.** W chwili podlewania $D_r/RAW$ ma medianę 0,89 (kwartyle 0,84–0,95). Roślina nie wchodzi w stres, a gleba nie jest przelewana.
- **Wykorzystuje bliską prognozę.** 8 mm w ciągu 2 dni wstrzymuje podlewanie, dopóki niedobór nie przekroczy progu stresu. 15 mm na jutro wstrzymuje je nawet przy suchej glebie. Opad za 4–6 dni (mało pewny) i drobny opad 3 mm nie zmieniają decyzji.

---

## 6. Trening: skąd agent to wie

### 6.1. Nagroda

Dzienna nagroda ([`simulator.daily_reward`](../ml/rl_irrigator/kowr_rl/simulator.py)):

$$
r_t = -K_y \frac{ET_{c,t} - ET_{a,t}}{ET_{c,sezon}^{est}} - c_w\, I_t - c_p\, DP_t,
\qquad c_w = 5 \cdot 10^{-4}\ \text{mm}^{-1},\quad c_p = 3 \cdot 10^{-4}\ \text{mm}^{-1}
$$

- **Pierwszy człon:** jego suma przez sezon to w przybliżeniu strata plonu z FAO-33, $1 - Y_a/Y_m$.
- **Cena wody:** 20 mm wody kosztuje tyle co 1 pkt proc. plonu, a 1 mm perkolacji tyle co 0,03 pkt proc.
- **Cel:** maksymalizacja $\mathbb{E}\left[\sum_t \gamma^t r_t\right]$ przy $\gamma = 0{,}995$.

### 6.2. Algorytm: MaskablePPO (sb3-contrib)

$$
\begin{aligned}
\delta_t &= r_t + \gamma V(s_{t+1}) - V(s_t), \qquad
\hat{A}_t = \sum_{l \ge 0} (\gamma\lambda)^l \delta_{t+l}, \quad \lambda = 0{,}95 \\
\rho_t &= \frac{\pi_{new}(a_t \mid s_t)}{\pi_{old}(a_t \mid s_t)} \\
L &= \mathbb{E}\Big[\min\big(\rho_t \hat{A}_t,\ \mathrm{clip}(\rho_t, 0{,}8, 1{,}2)\,\hat{A}_t\big)\Big] - 0{,}5\,(V - R)^2 + 0{,}001 \cdot H(\pi)
\end{aligned}
$$

### 6.3. Etapy treningu

1. **Start od reguły agronomicznej.** Na 8000 sezonach sieć uczy się naśladować regułę „10 mm przy $D_r > 0{,}9 \cdot RAW$, ale nie przed opadem ≥ 5 mm w ciągu 2 dni”:

   $$\min\ -\log \pi(a_{reguły} \mid o) + 0{,}5\,(V(o) - G)^2,$$

   gdzie $G$ to zdyskontowany zwrot nagród z sezonu.
2. **PPO.** 3 mln dni symulacji na 50 punktach rolnych z całej Polski w latach 2016–2023, dla 4 upraw i 4 kategorii gleby. Budżet losowany z $U[0, 250]$ mm, a w 30% epizodów z $U[20, 100]$ mm, żeby agent dobrze opanował ciasne budżety.
3. **Realistyczna prognoza w treningu.** Szum jest dopasowany do prawdziwych prognoz ECMWF z 2024 roku ($j$ = dzień wyprzedzenia):

   $$
   F_j = P \cdot 0{,}85 \cdot e^{\sigma_j \varepsilon},\quad \sigma_j = 0{,}85 + 0{,}04\,(j-1),\quad \varepsilon \sim \mathcal{N}(0, 1)
   $$
   - **Pominięty deszcz** z prawdopodobieństwem $0{,}13 + 0{,}04\,(j-1)$: $F_j = P \cdot U(0;\ 0{,}2)$.
   - **Fałszywy alarm** w suchy dzień z prawdopodobieństwem $0{,}08 + 0{,}03\,(j-1)$: $F_j \mathrel{+}= \mathrm{Exp}(3{,}3 + 0{,}35\,(j-1))$.

   Dzięki temu agent uczy się, jak bardzo ufać prognozie na kolejne dni. W walidacji i teście dostaje prawdziwe archiwalne prognozy ECMWF.
4. **Wybór modelu.** Spośród zapisanych w trakcie treningu wersji wdrażana jest ta z najlepszą nagrodą na walidacji 2024, która przy każdym budżecie (50 / 100 / 150 / 250 mm) ma plon ≥ reguła FAO i zużycie wody ≤ reguła FAO.

---

## 7. Jak powstaje rekomendacja w API

[`inference.recommend`](../ml/rl_irrigator/kowr_rl/inference.py), endpoint `POST /rl/recommend`:

1. **Odtworzenie sezonu.** Bilans liczony jest od 1 marca do dziś na prawdziwej pogodzie działki. Wcześniejsze nawodnienia pochodzą z podanej historii, a bez niej moduł rozkłada zużyty budżet zgodnie z polityką.
2. **Plan na 7 dni.** Codziennie agent wybiera dawkę, potem bilans jest przeliczany na prognozowanej pogodzie i następuje decyzja na kolejny dzień.
3. **Scenariusze.** Ten sam plan liczony jest dla opadu $\times 0$, $\times 1$ i $\times 1{,}5$ prognozy, co pokazuje, jak plan zależy od trafności prognozy.
4. **Uzasadnienie liczbami:**

   $$
   \text{zapas} = 100\left(1 - \frac{D_r}{TAW}\right)\%,\qquad
   \text{próg stresu} = 100\left(1 - \frac{RAW}{TAW}\right)\%,\qquad
   \text{dni do stresu} = \frac{RAW - D_r}{K_c \cdot \frac{1}{7}\sum ET_{0,-7..-1}}
   $$

   Do tego prawdopodobieństwa akcji $\pi(a)$, decyzja reguły FAO dla porównania, źródła z datami pobrania i tryb danych (LIVE / cache).

**Przykład** ([`examples/recommend_ziemniak_dzialka_2024.json`](../ml/rl_irrigator/examples/recommend_ziemniak_dzialka_2024.json)): ziemniak na działce 302111_5.0015.72.
- Zapas 53% $TAW$ przy progu stresu 67% $TAW$ (ziemniak ma niskie $p = 0{,}35$).
- Prognoza na 3 dni to 5 mm, za mało, żeby czekać.
- Decyzja: **„Nawadniaj dziś: 10 mm”**.

---

## 8. Efekt: test na 50 nowych lokalizacjach (2025–2026)

Prawdziwe prognozy ECMWF, 6400 sezonów na strategię, 95% przedziały bootstrap. Pełny raport: [`evaluation_holdout.md`](../ml/rl_irrigator/reports/evaluation_holdout.md).

| Budżet | Plon: agent − reguła FAO [pkt proc.] | Woda: agent − reguła FAO [mm] |
|---|---|---|
| łącznie | **+0,23** [+0,14; +0,33] | **−6,0** [−7,3; −5,1] |
| 50 mm | +0,12 [+0,07; +0,17] | −0,9 [−1,4; −0,5] |
| 100 mm | +0,26 [+0,12; +0,43] | −3,9 [−5,2; −2,8] |
| 150 mm | +0,28 [+0,13; +0,44] | −8,2 [−10,0; −6,5] |
| 250 mm | +0,27 [+0,20; +0,33] | −11,2 [−13,2; −9,6] |

Przy każdym budżecie agent daje istotnie wyższy plon przy istotnie mniejszym zużyciu wody.

---

## 9. Gdzie to jest w kodzie

| Element | Plik |
|---|---|
| Bilans FAO-56, plon FAO-33, obserwacja agenta, nagroda | [`kowr_rl/simulator.py`](../ml/rl_irrigator/kowr_rl/simulator.py) |
| Środowisko Gymnasium z maską budżetu | [`kowr_rl/env.py`](../ml/rl_irrigator/kowr_rl/env.py) |
| Trening (start od reguły, PPO, wybór modelu) | [`kowr_rl/train.py`](../ml/rl_irrigator/kowr_rl/train.py) |
| Szum prognozy | [`kowr_rl/forecast.py`](../ml/rl_irrigator/kowr_rl/forecast.py) |
| Reguły bazowe i wyrocznia | [`kowr_rl/baselines.py`](../ml/rl_irrigator/kowr_rl/baselines.py) |
| Sieć w NumPy | [`kowr_rl/policy.py`](../ml/rl_irrigator/kowr_rl/policy.py) |
| Rekomendacja i API `/rl/recommend`, `/rl/compare` | [`kowr_rl/inference.py`](../ml/rl_irrigator/kowr_rl/inference.py), [`kowr_rl/api.py`](../ml/rl_irrigator/kowr_rl/api.py) |
| Parametry upraw, gleb, nagrody i szumu | [`config/`](../ml/rl_irrigator/config/) |
