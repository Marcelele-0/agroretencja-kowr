"""Generic ordered registry used for every extensible part of the analysis.

One pattern for all "lists of things" in the engine: features (variables), WaterScore
components, veto rules, confidence checks, retention measures, farmer advice rules, data
sources and irrigation providers. Adding a new item = one decorated function or class in one
place; no ``if/elif`` chains to edit elsewhere.

Example::

    SCORE_COMPONENTS: Registry[ScoreComponent] = Registry("składniki WaterScore")

    @SCORE_COMPONENTS.register("gleba")
    def soil_component(fx: FeatureContext) -> float | None: ...
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Generic, TypeVar

T = TypeVar("T")


class RegistryError(KeyError):
    """Unknown or duplicated registry key."""


class Registry(Generic[T]):
    """Name -> item map that keeps registration order (order = evaluation / display order)."""

    def __init__(self, label: str) -> None:
        self.label = label
        self._items: dict[str, T] = {}

    def add(self, name: str, item: T, *, replace: bool = False) -> T:
        """Register ``item`` under ``name``. ``replace=True`` allows overriding (tests, plugins)."""
        if name in self._items and not replace:
            raise RegistryError(f"{self.label}: klucz '{name}' jest już zarejestrowany")
        self._items[name] = item
        return item

    def register(self, name: str, *, replace: bool = False) -> Callable[[T], T]:
        """Decorator form of :meth:`add`."""

        def deco(item: T) -> T:
            return self.add(name, item, replace=replace)

        return deco

    def remove(self, name: str) -> None:
        """Unregister an item (used by tests)."""
        self._items.pop(name, None)

    def get(self, name: str) -> T:
        try:
            return self._items[name]
        except KeyError as exc:
            known = ", ".join(self._items) or "(pusty)"
            raise RegistryError(f"{self.label}: brak '{name}'. Dostępne: {known}") from exc

    def __contains__(self, name: object) -> bool:
        return name in self._items

    def __iter__(self) -> Iterator[str]:
        return iter(self._items)

    def __len__(self) -> int:
        return len(self._items)

    def items(self) -> list[tuple[str, T]]:
        return list(self._items.items())

    def values(self) -> list[T]:
        return list(self._items.values())

    def names(self) -> list[str]:
        return list(self._items)
