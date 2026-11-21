# Notatki do slajdu: krok 4, nawadnianie (agent RL)

## Co powiedzieć (3–5 zdań)

1. Nawadnianie to ostatni krok, po retencji i agrotechnice. Moduł mówi rolnikowi, czy podlać w najbliższych 7 dniach i ile mm. Liczy to z dobowego bilansu wody FAO-56 dla jego działki: pogoda Open-Meteo, gleba IUNG-PIB, prognoza ECMWF.
2. Agenta uczenia ze wzmocnieniem (MaskablePPO) wytrenowaliśmy na 50 punktach rolnych z całej Polski i sezonach 2016–2023. Uczy się rozkładać ograniczony budżet wody: podawać małe dawki, zostawiać w glebie miejsce na deszcz i nie podlewać przed opadem z prognozy.
3. Sprawdziliśmy go na **50 nowych lokalizacjach**, których model nigdy nie widział, w latach 2025–2026 i z prawdziwymi archiwalnymi prognozami ECMWF. **Przy każdym budżecie wody agent daje wyższy plon i zużywa mniej wody niż standardowa reguła FAO-56.** Łącznie to −7% wody (81 wobec 87 mm na sezon) przy +0,23 pkt proc. plonu, a obie różnice są istotne.
4. Agent wygrywa też z najlepszą prostą regułą, jaką znaleźliśmy (małe dawki + prognoza). Zbiera ok. 55–60% tego, co dałaby nawet idealna znajomość przyszłej pogody. Pokazujemy to otwarcie, razem z przedziałami niepewności, najgorszym przypadkiem agenta i 10-letnim porównaniem strategii dla każdej działki.

## Dwa najważniejsze wykresy

1. `reports/figures/strategies_holdout.png`: plon i zużycie wody siedmiu strategii na nowych lokalizacjach (bez nawadniania, kalendarz, FAO, FAO + prognoza, FAO małe dawki, RL, wyrocznia), z 95% przedziałami.
2. `reports/figures/budget_curves_holdout.png`: plon i woda w zależności od budżetu. Agent ma najmniej wody przy każdym budżecie i nie traci na plonie.

(Alternatywa dla 2: `reports/figures/example_seasons_holdout.png`, czyli cztery prawdziwe sezony RL wobec FAO, w tym celowo najgorszy przypadek agenta.)

## Liczby na slajd

- Czysty test: 6400 sezonów na strategię (50 nowych punktów × 2 lata × 4 uprawy × 4 gleby × 4 budżety).
- RL wobec FAO: +0,23 pkt proc. plonu, −6,0 mm wody na sezon. Przy budżetach 50 / 100 / 150 / 250 mm: −0,9 / −3,9 / −8,2 / −11,2 mm wody i za każdym razem wyższy plon (wszystko istotne).
- Wobec najlepszej prostej reguły: +0,11 pkt proc., −2,0 mm.
- Odpowiedź API: mediana poniżej 0,5 s na zimno, ok. 0,01 s z cache.
- Runtime bez PyTorcha: polityka w czystym NumPy (157 KiB).

## Jeśli jury zapyta

- **„Czy to nie przeuczenie?”** Model wybraliśmy tylko na roku walidacyjnym 2024. Kryterium sukcesu zapisaliśmy przed testem. Test jest na nowych lokalizacjach, a 3 ziarna dają ten sam wynik.
- **„Gdzie agent jest słaby?”** Pszenica ozima na najlżejszej glebie: średnio −0,19 pkt proc. plonu za −12 mm wody, czasem zostawia budżet na koniec sezonu.
- **„Ile to w ogóle można zyskać?”** Nawet wyrocznia znająca pogodę jest tylko o 11 mm wody i 0,5 pkt proc. plonu lepsza od FAO. W tym modelu reguła FAO jest już blisko optimum, a agent zbiera ponad połowę reszty.
