"""Common contract for all external data source clients.

Every client:
- has its own time limit and never raises into the analysis pipeline,
- reports how long the fetch took (shown in the report footer),
- marks the result as live / pre_fetched / fallback / brak_danych with a plain-language reason,
- builds a ``SourceAuditEntry`` with licence and mandatory attribution.
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import UTC, datetime
from time import perf_counter
from typing import Any, ClassVar, Generic, TypeVar

import httpx

from backend.clients.cache import SourceCache
from backend.config import Settings
from hydro_core.schemas import DataMode, SourceAuditEntry

logger = logging.getLogger(__name__)

T = TypeVar("T")


class SourceUnavailableError(Exception):
    """Raised by a client when the source cannot deliver data; message is shown to the farmer."""


@dataclass(frozen=True)
class SourceDescriptor:
    """Static metadata of a data source used for the audit trail."""

    key: str
    category: str
    source_name: str
    endpoint: str
    spatial_resolution: str
    license: str
    attribution: str | None = None


@dataclass(frozen=True)
class FetchOutcome(Generic[T]):
    """Value returned by ``_fetch`` implementations."""

    data: T
    mode: DataMode = DataMode.LIVE
    observation_date: str = ""
    message: str | None = None
    source_name: str | None = None
    endpoint: str | None = None
    spatial_resolution: str | None = None
    license: str | None = None
    attribution: str | None = None


@dataclass
class SourceResult(Generic[T]):
    """Outcome of one data source call, always present even when the source failed."""

    key: str
    data: T | None
    mode: DataMode
    fetch_time_s: float
    message: str | None
    audit: SourceAuditEntry

    @property
    def ok(self) -> bool:
        """True when data is available (live, cached or fallback)."""
        return self.data is not None


class BaseSourceClient(ABC, Generic[T]):
    """Base class with timing, timeout and error isolation."""

    descriptor: ClassVar[SourceDescriptor]

    def __init__(self, http: httpx.AsyncClient, cache: SourceCache, settings: Settings) -> None:
        self.http = http
        self.cache = cache
        self.settings = settings

    @property
    @abstractmethod
    def timeout_s(self) -> float:
        """Hard time limit for this source."""

    @abstractmethod
    async def _fetch(self, **kwargs: Any) -> FetchOutcome[T]:
        """Fetch and parse data. Raise SourceUnavailableError with a clear Polish message."""

    async def run(self, **kwargs: Any) -> SourceResult[T]:
        """Run the fetch with timeout and convert every failure into a 'brak danych' result."""
        started = perf_counter()
        try:
            outcome = await asyncio.wait_for(self._fetch(**kwargs), timeout=self.timeout_s)
        except TimeoutError:
            return self.missing(
                f"Źródło nie odpowiedziało w limicie {self.timeout_s:.0f} s.",
                perf_counter() - started,
            )
        except SourceUnavailableError as exc:
            return self.missing(str(exc), perf_counter() - started)
        except httpx.HTTPStatusError as exc:
            return self.missing(
                f"Serwer zwrócił błąd HTTP {exc.response.status_code}.", perf_counter() - started
            )
        except httpx.HTTPError as exc:
            return self.missing(
                f"Błąd połączenia ({type(exc).__name__}).", perf_counter() - started
            )
        except Exception:  # a broken source must never stop the analysis
            logger.exception("Unexpected error in source %s", self.descriptor.key)
            return self.missing("Nieoczekiwany błąd przetwarzania danych.", perf_counter() - started)

        elapsed = perf_counter() - started
        return SourceResult(
            key=self.descriptor.key,
            data=outcome.data,
            mode=outcome.mode,
            fetch_time_s=round(elapsed, 2),
            message=outcome.message,
            audit=self._audit(
                mode=outcome.mode,
                observation_date=outcome.observation_date,
                elapsed=elapsed,
                message=outcome.message,
                outcome=outcome,
            ),
        )

    def missing(self, message: str, elapsed: float = 0.0) -> SourceResult[T]:
        """Build an explicit 'brak danych' result."""
        return SourceResult(
            key=self.descriptor.key,
            data=None,
            mode=DataMode.MISSING,
            fetch_time_s=round(elapsed, 2),
            message=message,
            audit=self._audit(
                mode=DataMode.MISSING, observation_date="brak", elapsed=elapsed, message=message
            ),
        )

    def _audit(
        self,
        mode: DataMode,
        observation_date: str,
        elapsed: float,
        message: str | None,
        outcome: FetchOutcome[Any] | None = None,
    ) -> SourceAuditEntry:
        d = self.descriptor
        return SourceAuditEntry(
            category=d.category,
            source_name=(outcome.source_name if outcome and outcome.source_name else d.source_name),
            endpoint_or_ref=(outcome.endpoint if outcome and outcome.endpoint else d.endpoint),
            mode=mode,
            retrieved_at=datetime.now(UTC),
            observation_date=observation_date or "brak",
            spatial_resolution=(
                outcome.spatial_resolution
                if outcome and outcome.spatial_resolution
                else d.spatial_resolution
            ),
            license=(outcome.license if outcome and outcome.license else d.license),
            fetch_time_s=round(elapsed, 2),
            attribution=(outcome.attribution if outcome and outcome.attribution else d.attribution),
            message=message,
        )