# Notatki do slajdu: krok 4, nawadnianie (agent RL)

## Co powiedzieć (3–5 zdań)

1. Nawadnianie to ostatni krok, po retencji i agrotechnice. Moduł mówi rolnikowi, czy podlać w najbliższych 7 dniach i ile mm. Liczy to z dobowego bilansu wody FAO-56 dla jego działki: pogoda Open-Meteo, gleba IUNG-PIB, prognoza ECMWF.
2. Agenta uczenia ze wzmocnieniem (MaskablePPO) wytrenowaliśmy na 50 punktach rolnych z całej Polski i sezonach 2016–2023. Uczy się rozkładać ograniczony budżet wody: nie podlewać przed deszczem i nie lać wody na wilgotną glebę.
3. Na latach testowych 2025–2026, z prawdziwymi archiwalnymi prognozami ECMWF, agent zużył **o 7% mniej wody niż reguła FAO-56 (80 wobec 86 mm na sezon) przy nieco wyższym plonie**. To ok. 55% oszczędności, jaką dałaby nawet idealna znajomość przyszłej pogody.
4. Przy bardzo małym budżecie (50 mm) wynik był remisowy. Zgodnie z kryterium ustalonym przed testem w aplikacji domyślna jest więc sprawdzona reguła FAO-56, a agent działa jako tryb eksperymentalny. Pokazujemy to otwarcie, razem z niepewnością i 10-letnim porównaniem strategii dla każdej działki.

## Dwa najważniejsze wykresy

1. `reports/figures/strategies_test.png`: plon i zużycie wody sześciu strategii (bez nawadniania, kalendarz, FAO, FAO + prognoza, RL, wyrocznia) na latach testowych, z 95% przedziałami.
2. `reports/figures/example_seasons.png`: cztery prawdziwe sezony RL wobec FAO (zapas wody, opady, dawki, woda skumulowana), w tym celowo najgorszy przypadek agenta.

(Alternatywa dla 2: `reports/figures/budget_curves_test.png`, czyli plon i woda w zależności od budżetu.)

## Liczby na slajd

- Test: 6400 sezonów na strategię (50 punktów × 2 lata × 4 uprawy × 4 gleby × 4 budżety).
- RL wobec FAO: +0,09 pkt proc. plonu, −5,9 mm wody (oba istotne); przy budżecie 250 mm −14,7 mm.
- Odpowiedź API: mediana poniżej 0,5 s na zimno, ok. 0,04 s z cache.
- Runtime bez PyTorcha: polityka w czystym NumPy (151 KiB).
