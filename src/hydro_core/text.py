"""Polish text helpers for farmer-facing messages (decimal comma, plural forms)."""

from __future__ import annotations


def num(value: float, digits: int = 0, sign: bool = False) -> str:
    """Number with a decimal comma, e.g. num(3.45, 1) -> '3,5', num(2, 1, sign=True) -> '+2,0'."""
    text = f"{value:+.{digits}f}" if sign else f"{value:.{digits}f}"
    return text.replace(".", ",").replace("-", "−")


def plural(n: float, one: str, few: str, many: str) -> str:
    """Polish plural: 1 dzień, 2-4 dni, 5 dni (one/few/many forms)."""
    k = abs(round(n))
    if k == 1:
        return one
    if k % 10 in (2, 3, 4) and k % 100 not in (12, 13, 14):
        return few
    return many


def days(n: float) -> str:
    """'1 dzień', '3 dni', '12 dni'."""
    return f"{round(n)} {plural(n, 'dzień', 'dni', 'dni')}"


def effect_text(yield_pp: float, reserve_days: float, retained_runoff_mm: float = 0.0) -> str:
    """Simulated gains of a retention measure in words, each with its own sign; zero effects are left out."""
    parts = []
    if yield_pp >= 0.05:
        parts.append(f"strata plonu mniejsza o {num(yield_pp, 1)} p.p.")
    if reserve_days >= 1:
        n = round(reserve_days)
        parts.append(f"{n} {plural(n, 'dzień', 'dni', 'dni')} stresu wodnego mniej")
    if retained_runoff_mm >= 0.5:
        parts.append(f"spływ mniejszy o {num(retained_runoff_mm)} mm")
    return ", ".join(parts)


def whatif_text(baseline_loss_pct: float, scenario_loss_pct: float, reserve_days: float, saved_water_mm: float,
                retained_runoff_mm: float = 0.0, digits: int = 0) -> str:
    """What-if result as on the page: 'strata plonu 55% → 49%, dni stresu −3, woda dla roślin +25 mm, spływ −12 mm'."""
    parts = [f"strata plonu {num(baseline_loss_pct, digits)}% → {num(scenario_loss_pct, digits)}%"]
    if round(reserve_days):
        parts.append(f"dni stresu {num(-reserve_days, sign=True)}")
    if abs(saved_water_mm) >= 0.5:
        parts.append(f"woda dla roślin {num(saved_water_mm, sign=True)} mm")
    if retained_runoff_mm >= 0.5:
        parts.append(f"spływ {num(-retained_runoff_mm, sign=True)} mm")
    return ", ".join(parts)


# Same words as the browser scale (frontend/src/ds/status.tsx)
_STATE_LABELS = {
    -5: "susza skrajna", -4: "susza silna", -3: "susza", -2: "niedobór wody", -1: "lekki niedobór", 0: "optimum",
    1: "lekki nadmiar", 2: "nadmiar wody", 3: "przewilgocenie", 4: "silne przewilgocenie", 5: "zalanie",
}


def state_label(state: float) -> str:
    """Word for a water state on the −5…+5 axis, e.g. state_label(-1.2) -> 'lekki niedobór'."""
    return _STATE_LABELS[max(-5, min(5, round(state)))]
