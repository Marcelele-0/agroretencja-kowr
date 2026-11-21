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
