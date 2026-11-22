"""Two-page auditable report (step 5): HTML template -> PDF through a chain of renderers.

``RENDERERS`` order: WeasyPrint (full layout; needs GTK/Pango system libraries) -> Matplotlib
(pure-Python PDF with the same content in a simpler layout; works on any Windows) -> HTML.
The first renderer that works is used; the response header tells which one.
"""

from __future__ import annotations

import base64
import datetime as dt
import io
import logging
import textwrap
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from jinja2 import Environment, FileSystemLoader, select_autoescape
from matplotlib.backends.backend_pdf import PdfPages

from hydro_core.crops import crop_params
from hydro_core.registry import Registry
from hydro_core.schemas import AnalysisResponse
from hydro_core.topography import TerrainAnalysis, terrain_wetness_offset

logger = logging.getLogger(__name__)

TEMPLATES = Path(__file__).resolve().parent / "templates"
A4_INCHES = (8.27, 11.69)


@dataclass(frozen=True)
class RenderedReport:
    content: bytes
    media_type: str
    renderer: str


class RendererUnavailable(Exception):
    """The renderer cannot run on this host (missing system library)."""


Renderer = Callable[[str, AnalysisResponse, dict[str, str | None]], RenderedReport]
RENDERERS: Registry[Renderer] = Registry("renderery raportu")


@lru_cache(maxsize=1)
def _env() -> Environment:
    return Environment(loader=FileSystemLoader(TEMPLATES), autoescape=select_autoescape(["html", "j2"]))


def _png(fig: plt.Figure) -> str:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=130, bbox_inches="tight")
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def history_chart(r: AnalysisResponse) -> plt.Figure:
    a = r.assessment
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(7.4, 2.1))
    years = [h.year for h in a.history_10y]
    ax1.bar(years, [h.yield_loss_pct for h in a.history_10y], color=["#e0a35a" if h.is_partial else "#c0603a" for h in a.history_10y])
    ax1.set_title("Strata plonu z niedoboru wody [%] (FAO-33)", fontsize=8)
    ax1.tick_params(labelsize=7)
    pts = a.water_state_timeline
    if pts:
        xs = [p.date for p in pts]
        ys = [p.value for p in pts]
        fc = [p.forecast for p in pts]
        ax2.plot([x for x, f in zip(xs, fc, strict=True) if not f], [y for y, f in zip(ys, fc, strict=True) if not f], color="#2f7d55", label="stan")
        ax2.plot([x for x, f in zip(xs, fc, strict=True) if f], [y for y, f in zip(ys, fc, strict=True) if f], "--", color="#2f7d55", label="projekcja")
        ax2.axhline(0, color="#999", lw=0.6)
        ax2.set_ylim(-5, 5)
        ax2.legend(fontsize=6)
    ax2.set_title("Stan wody −5…+5 wokół daty analizy", fontsize=8)
    ax2.tick_params(labelsize=6)
    fig.autofmt_xdate()
    return fig


def terrain_map(terrain: TerrainAnalysis | None, water_state: float) -> plt.Figure | None:
    if terrain is None:
        return None
    offset = terrain_wetness_offset(terrain)
    state = np.clip(water_state + offset, -5, 5)
    fig, ax = plt.subplots(figsize=(3.4, 3.4))
    ax.imshow(np.where(terrain.mask, state, np.nan), cmap="RdYlBu", vmin=-5, vmax=5)
    ax.contour(terrain.elevation, levels=8, colors="#555", linewidths=0.3)
    ax.set_title("Gdzie ucieka woda: strefy −5…+5 (NMT)", fontsize=7)
    ax.set_xticks([])
    ax.set_yticks([])
    return fig


def build_html(r: AnalysisResponse, images: dict[str, str | None]) -> str:
    return _env().get_template("report.html.j2").render(
        r=r, a=r.assessment, crop_label=crop_params(r.assessment.crop).label_pl, irrigation_text=irrigation_text(r),
        generated=dt.datetime.now().strftime("%Y-%m-%d %H:%M"), chart=images.get("chart"), map_png=images.get("map"),
    )


@lru_cache(maxsize=1)
def _weasyprint_html_class() -> type | None:
    """Import WeasyPrint once per process (a failed import prints a long warning every time)."""
    try:
        from weasyprint import HTML
    except (ImportError, OSError):  # OSError: GTK / Pango libraries missing (typical on Windows)
        logger.info("WeasyPrint unavailable (missing GTK/Pango) - using the Matplotlib PDF renderer")
        return None
    return HTML


@RENDERERS.register("weasyprint")
def _weasyprint(html: str, r: AnalysisResponse, images: dict[str, str | None]) -> RenderedReport:
    html_cls = _weasyprint_html_class()
    if html_cls is None:
        raise RendererUnavailable("WeasyPrint: brak bibliotek GTK/Pango")
    return RenderedReport(html_cls(string=html).write_pdf(), "application/pdf", "weasyprint")


def irrigation_text(r: AnalysisResponse) -> str:
    """Action + rationale without repeating the action when the rationale already starts with it."""
    action, why = r.irrigation.action_now, r.irrigation.rationale
    return why if why.lower().startswith(action.lower()) else f"{action} – {why}"


def _text_page(pdf: PdfPages, lines: list[tuple[str, float, str]], image: str | None = None) -> None:
    fig = plt.figure(figsize=A4_INCHES)
    y = 0.965
    for text, size, weight in lines:
        # usable width ~ 0.88 x 8.27 in = 524 pt; DejaVu Sans averages ~0.55 em per character
        for chunk in textwrap.wrap(text, width=int(940 / size)) or [""]:
            fig.text(0.06, y, chunk, fontsize=size, weight=weight, va="top", family="DejaVu Sans")
            y -= size / 72.0 / A4_INCHES[1] * 1.45
        if y < 0.05:
            break
    if image:
        img = plt.imread(io.BytesIO(base64.b64decode(image)), format="png")
        ax = fig.add_axes([0.06, 0.04, 0.88, max(0.05, y - 0.06)])
        ax.imshow(img)
        ax.axis("off")
    pdf.savefig(fig)
    plt.close(fig)


@RENDERERS.register("matplotlib")
def _matplotlib(html: str, r: AnalysisResponse, images: dict[str, str | None]) -> RenderedReport:
    a = r.assessment
    p = r.parcel
    H, H2, T = 13.0, 10.5, 8.0
    page1: list[tuple[str, float, str]] = [
        (f"Ocena wody na działce {p.parcel_id}", H, "bold"),
        (f"{p.voivodeship or ''} · {p.county or ''} · {p.commune or ''} · {p.area_ha:.2f} ha · {crop_params(a.crop).label_pl} · {a.analysis_date}", T, "normal"),
        ("1. Ocena sytuacji wodnej", H2, "bold"),
        (f"Stan wody {a.water_state:+.1f} (−5…+5), WaterScore {a.water_score.total:.0f}/100, ryzyko {a.water_risk.level.value}, pewność {a.confidence.level.value}.", T, "normal"),
        (a.summary_text, T, "normal"),
        *[(f"Weto: {v}", T, "normal") for v in a.water_risk.veto_reasons],
        ("2. Przyczyny niedoboru", H2, "bold"),
        (f"Klimat {r.causes.climate_pct:.0f}% · gleba {r.causes.soil_pct:.0f}% · spływ {r.causes.runoff_pct:.0f}% · uprawa {r.causes.crop_pct:.0f}% ({r.causes.method})", T, "normal"),
        *[(f"– {v}", T, "normal") for v in r.causes.explanations.values()],
        (f"3. Ranking działań retencyjnych (rok odniesienia {r.retention.what_if_default.reference_dry_year})", H2, "bold"),
        *[(f"{m.priority_rank}. {m.title}: −{m.effect_yield_pp:.1f} p.p. straty plonu, {m.effect_reserve_days} dni stresu mniej"
           + (" – wymaga zgody Wód Polskich" if m.requires_permit else ""), T, "normal") for m in r.retention.recommendations[:8]],
    ]
    page2: list[tuple[str, float, str]] = [
        ("4. Nawadnianie", H2, "bold"),
        (irrigation_text(r), T, "normal"),
        *[(f"{s.strategy_name}: strata {s.avg_yield_loss_pct:.1f}%, woda {s.avg_water_mm:.0f} mm, przesiąk {s.avg_deep_percolation_mm:.0f} mm", T, "normal") for s in r.irrigation.strategies_10y],
        ("Porady", H2, "bold"),
        *[(f"[{adv.priority}] {adv.title}: {adv.text}", T, "normal") for adv in r.advice],
        ("5. Źródła danych i niepewność", H2, "bold"),
        *[(f"{s.category}: {s.source_name} – {s.mode.value}, {s.observation_date}, {s.spatial_resolution}, {s.license}", 7.0, "normal") for s in r.audit.sources],
        *[(f"! {x}", 7.0, "normal") for x in a.confidence.resolution_warnings + a.confidence.consistency_warnings + a.confidence.missing_factors],
        (f"Czas analizy {r.audit.execution_time_s:.1f} s. {r.audit.rodo_notice}", 7.0, "normal"),
    ]
    buf = io.BytesIO()
    with PdfPages(buf) as pdf:
        _text_page(pdf, page1, images.get("chart"))
        _text_page(pdf, page2, images.get("map"))
    return RenderedReport(buf.getvalue(), "application/pdf", "matplotlib")


@RENDERERS.register("html")
def _html(html: str, r: AnalysisResponse, images: dict[str, str | None]) -> RenderedReport:
    return RenderedReport(html.encode("utf-8"), "text/html; charset=utf-8", "html")


def render_report(r: AnalysisResponse, terrain: TerrainAnalysis | None = None) -> RenderedReport:
    images: dict[str, str | None] = {"chart": None, "map": None}
    try:
        images["chart"] = _png(history_chart(r))
        fig = terrain_map(terrain, r.assessment.water_state)
        images["map"] = _png(fig) if fig is not None else None
    except Exception:
        logger.warning("Report images failed", exc_info=True)
    html = build_html(r, images)
    for name, renderer in RENDERERS.items():
        try:
            return renderer(html, r, images)
        except RendererUnavailable as exc:
            logger.info("Renderer %s unavailable: %s", name, exc)
        except Exception:
            logger.warning("Renderer %s failed", name, exc_info=True)
    raise RuntimeError("Nie udało się wygenerować raportu.")
