# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Mark Rusch
"""Typed loaders for the methodology config.

factors.yaml is the single source of truth for every methodology parameter;
nothing numeric that affects a print may be hard-coded elsewhere.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date as date_type
from pathlib import Path
from typing import Any

import yaml

CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"
SUCCESSION_PATH = CONFIG_DIR / "methodology" / "succession.yaml"


@dataclass(frozen=True)
class ReferenceUnit:
    gpu_model: str
    term: str
    location: str


@dataclass(frozen=True)
class Filters:
    min_gpu_count: int
    price_floor_usd: float
    price_ceiling_usd: float
    datacenter_verified_only: bool
    exclude_tiers: tuple[str, ...]


@dataclass(frozen=True)
class ModelClass:
    reference_variant: str
    variants: dict[str, float]  # variant name -> multiplicative adjustment factor


@dataclass(frozen=True)
class WeightReview:
    anchor_weekday: int  # ISO weekday a review takes effect (1 = Monday)
    window_days: int
    min_history_days: int


@dataclass(frozen=True)
class Weights:
    capacity_cap: int
    default_capacity: int
    executable_multiplier: float
    max_weight_share_pct: float
    review: WeightReview


@dataclass(frozen=True)
class Composite:
    base_value: float
    min_class_share_pct: float
    max_class_share_pct: float


@dataclass(frozen=True)
class TrimRule:
    """One step of the count-based trim ladder: for n >= min_n, clamp k from each end."""

    min_n: int
    k: int


@dataclass(frozen=True)
class Aggregation:
    unit: str  # 'offer' — the aggregation unit (v0.3.0: offers, not one price per provider)
    estimator: str
    trim_k: tuple[TrimRule, ...]
    min_providers: int
    min_offers: int
    smoothing_days: int

    def trim_for(self, n: int) -> int:
        """k for a panel of n observations — the last rule whose min_n is satisfied.

        Count-based because percentile winsorising is inert at small n: at n=6 the
        nearest-rank p5/p95 boundaries are the min and max, so nothing is clamped.
        """
        k = 0
        for rule in self.trim_k:
            if n >= rule.min_n:
                k = rule.k
        return min(k, max(0, (n - 1) // 2))  # never trim away the whole panel


@dataclass(frozen=True)
class Continuity:
    publish_chained: bool
    base_value: float
    divergence_review_pct: float


@dataclass(frozen=True)
class Fx:
    max_age_days: int
    # False: the latest rate dated on or before the print date (to v0.4.0). True: strictly
    # before, so the EUR leg is T-1 whenever the run happens (from v0.5.0).
    strictly_before: bool = False


@dataclass(frozen=True)
class Staleness:
    warn_days: int
    exclude_days: int


@dataclass(frozen=True)
class PanelEntry:
    """One provider on the explicit panel: its segment and what it may contribute.

    `sources` maps a collector name to the model classes that collector's rows may enter.
    A provider observed through a source, or in a class, that is not listed here is
    collected and stored but cannot reach a print: it appears in the audit set as
    `not_in_panel`. Adding a (source, class) pair is a constituent change and follows
    GOVERNANCE.md §1.
    """

    segment: str
    sources: dict[str, frozenset[str]]


@dataclass(frozen=True)
class RegionalSeries:
    """A series priced exactly like the headline family, in another region block."""

    block: str
    model_class: str
    population: str  # a series_populations role, e.g. 'headline'


@dataclass(frozen=True)
class BasisSeries:
    """A spread between two published series: value = lead - reference, per GPU-hour."""

    lead: str
    reference: str


@dataclass(frozen=True)
class Factors:
    methodology_version: str
    reference_unit: ReferenceUnit
    model_classes: dict[str, ModelClass]
    segments: dict[str, str]  # provider -> market segment
    series_populations: dict[str, tuple[str, ...]]  # series role -> segments drawn from
    filters: Filters
    weights: Weights
    composite: Composite
    aggregation: Aggregation
    continuity: Continuity
    fx: Fx
    staleness: Staleness
    jump_flag_pct: float
    measured_factors: dict[str, Any]
    eu_eea_countries: frozenset[str]
    # None only for parameter sets that predate the explicit panel; every version from
    # 0.3.0-dev onward carries one (0.3.0-dev's was made explicit on 2026-09-11).
    panel: dict[str, PanelEntry] | None = None
    # Region blocks other than EU/EEA (whose countries are eu_eea_countries), and the
    # series priced in them. Empty before v0.6.0.
    blocks: dict[str, frozenset[str]] = field(default_factory=dict)
    regional_series: dict[str, RegionalSeries] = field(default_factory=dict)
    basis_series: dict[str, BasisSeries] = field(default_factory=dict)

    def countries_of(self, block: str) -> frozenset[str]:
        if block == "EU_EEA":
            return self.eu_eea_countries
        return self.blocks[block]

    @property
    def headline_class(self) -> str:
        """The class whose reference variant is the headline reference unit."""
        for name, mc in self.model_classes.items():
            if mc.reference_variant == self.reference_unit.gpu_model:
                return name
        raise ValueError(
            f"no model class has reference_variant {self.reference_unit.gpu_model!r}"
        )

    def segment_of(self, provider: str) -> str:
        """Market segment for a provider.

        With an explicit panel the segment comes from the panel. Without one (legacy
        parameter sets only) unlisted providers default to 'neocloud', which is how a new
        collector could once reach the headline without anyone deciding it should: the
        reason the panel exists.
        """
        if self.panel is not None and provider in self.panel:
            return self.panel[provider].segment
        return self.segments.get(provider, "neocloud")

    def admits(self, provider: str, source: str, model_class: str) -> bool:
        """Whether a row from (provider, source) may enter a print of `model_class`."""
        if self.panel is None:
            return True
        entry = self.panel.get(provider)
        if entry is None:
            return False
        return model_class in entry.sources.get(source, frozenset())

    def population_for(self, role: str) -> frozenset[str]:
        """Segments a series role draws from."""
        return frozenset(self.series_populations[role])


@dataclass(frozen=True)
class StaticProvider:
    provider: str
    url: str
    gpu_model: str
    price_usd_per_gpu_hr: float | None
    gpu_count: int
    country: str
    config_notes: str = ""
    last_verified: str | None = None  # YYYY-MM-DD
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SuccessionEntry:
    version: str
    effective_from: str  # YYYY-MM-DD, the first print date computed under this version
    params_dir: Path
    notice: str | None
    reproducible: bool


def load_succession(path: Path | None = None) -> list[SuccessionEntry]:
    """Methodology versions in the order they take effect.

    Effective dates are enforced here, in code, rather than by merging a change on the
    right day: a version announced with a notice sits in the repository from the day it
    is announced and is selected for a print only once its effective date arrives.
    """
    succ_path = path or SUCCESSION_PATH
    raw = yaml.safe_load(succ_path.read_text(encoding="utf-8"))
    root = succ_path.parents[2]
    out = []
    for v in raw["versions"]:
        out.append(
            SuccessionEntry(
                version=str(v["version"]),
                effective_from=str(v["effective_from"]),
                params_dir=root / str(v["params"]),
                notice=str(v["notice"]) if v.get("notice") else None,
                reproducible=bool(v.get("reproducible", True)),
            )
        )
    return out


def version_for(on_date: str, succession: list[SuccessionEntry] | None = None) -> SuccessionEntry:
    """The version live on `on_date`: the last whose effective_from is on or before it."""
    entries = succession if succession is not None else load_succession()
    day = date_type.fromisoformat(on_date)
    live = [e for e in entries if date_type.fromisoformat(e.effective_from) <= day]
    if not live:
        raise ValueError(f"no methodology version is in effect on {on_date}")
    return max(live, key=lambda e: date_type.fromisoformat(e.effective_from))


def _params_dir(config_dir: Path | None, for_date: str | None) -> Path:
    if config_dir is not None:
        return config_dir
    if for_date is not None:
        return version_for(for_date).params_dir
    return CONFIG_DIR


def _parse_panel(raw: dict[str, Any] | None) -> dict[str, PanelEntry] | None:
    if raw is None:
        return None
    return {
        provider: PanelEntry(
            segment=str(entry["segment"]),
            sources={
                source: frozenset(str(c) for c in classes)
                for source, classes in entry["sources"].items()
            },
        )
        for provider, entry in raw.items()
    }


def load_factors(config_dir: Path | None = None, *, for_date: str | None = None) -> Factors:
    """The parameter set to use.

    `for_date` selects the version live on that print date (see load_succession); with
    neither argument the head of the succession, config/factors.yaml, is returned. The
    head may be a version that is announced but not yet in effect, so any code computing
    a print must pass `for_date`.
    """
    cfg_dir = _params_dir(config_dir, for_date)
    raw = yaml.safe_load((cfg_dir / "factors.yaml").read_text(encoding="utf-8"))
    panel = _parse_panel(raw.get("panel"))
    segments = (
        {p: e.segment for p, e in panel.items()}
        if panel is not None and "segments" not in raw
        else {
            provider: segment
            for segment, providers in raw["segments"].items()
            for provider in providers
        }
    )
    return Factors(
        methodology_version=str(raw["methodology_version"]),
        reference_unit=ReferenceUnit(
            gpu_model=str(raw["reference_unit"]["gpu_model"]),
            term=str(raw["reference_unit"]["term"]),
            location=str(raw["reference_unit"]["location"]),
        ),
        segments=segments,
        series_populations={
            role: tuple(segments) for role, segments in raw["series_populations"].items()
        },
        model_classes={
            name: ModelClass(
                reference_variant=str(mc["reference_variant"]),
                variants={k: float(v) for k, v in mc["variants"].items()},
            )
            for name, mc in raw["model_classes"].items()
        },
        filters=Filters(
            min_gpu_count=int(raw["filters"]["min_gpu_count"]),
            price_floor_usd=float(raw["filters"]["price_floor_usd"]),
            price_ceiling_usd=float(raw["filters"]["price_ceiling_usd"]),
            datacenter_verified_only=bool(raw["filters"]["datacenter_verified_only"]),
            exclude_tiers=tuple(raw["filters"]["exclude_tiers"]),
        ),
        weights=Weights(
            capacity_cap=int(raw["weights"]["capacity_cap"]),
            default_capacity=int(raw["weights"]["default_capacity"]),
            executable_multiplier=float(raw["weights"]["executable_multiplier"]),
            max_weight_share_pct=float(raw["weights"]["max_weight_share_pct"]),
            review=WeightReview(
                anchor_weekday=int(raw["weights"]["review"]["anchor_weekday"]),
                window_days=int(raw["weights"]["review"]["window_days"]),
                min_history_days=int(raw["weights"]["review"]["min_history_days"]),
            ),
        ),
        composite=Composite(
            base_value=float(raw["composite"]["base_value"]),
            min_class_share_pct=float(raw["composite"]["min_class_share_pct"]),
            max_class_share_pct=float(raw["composite"]["max_class_share_pct"]),
        ),
        aggregation=Aggregation(
            unit=str(raw["aggregation"]["unit"]),
            estimator=str(raw["aggregation"]["estimator"]),
            trim_k=tuple(
                TrimRule(min_n=int(r["min_n"]), k=int(r["k"]))
                for r in sorted(raw["aggregation"]["trim_k"], key=lambda r: int(r["min_n"]))
            ),
            min_providers=int(raw["aggregation"]["min_providers"]),
            min_offers=int(raw["aggregation"]["min_offers"]),
            smoothing_days=int(raw["aggregation"]["smoothing_days"]),
        ),
        continuity=Continuity(
            publish_chained=bool(raw["continuity"]["publish_chained"]),
            base_value=float(raw["continuity"]["base_value"]),
            divergence_review_pct=float(raw["continuity"]["divergence_review_pct"]),
        ),
        fx=Fx(
            max_age_days=int(raw["fx"]["max_age_days"]),
            strictly_before=bool(raw["fx"].get("strictly_before", False)),
        ),
        staleness=Staleness(
            warn_days=int(raw["staleness"]["warn_days"]),
            exclude_days=int(raw["staleness"]["exclude_days"]),
        ),
        jump_flag_pct=float(raw["quality"]["jump_flag_pct"]),
        measured_factors=dict(raw.get("measured_factors") or {}),
        eu_eea_countries=frozenset(raw["eu_eea_countries"]),
        panel=panel,
        blocks={
            name: frozenset(str(c) for c in countries)
            for name, countries in (raw.get("blocks") or {}).items()
        },
        regional_series={
            name: RegionalSeries(
                block=str(d["block"]), model_class=str(d["class"]),
                population=str(d["population"]),
            )
            for name, d in (raw.get("regional_series") or {}).items()
        },
        basis_series={
            name: BasisSeries(lead=str(d["lead"]), reference=str(d["reference"]))
            for name, d in (raw.get("basis_series") or {}).items()
        },
    )


def load_sovereign(
    config_dir: Path | None = None, *, for_date: str | None = None
) -> frozenset[str]:
    cfg_dir = _params_dir(config_dir, for_date)
    raw = yaml.safe_load((cfg_dir / "sovereign.yaml").read_text(encoding="utf-8"))
    return frozenset(raw["sovereign_providers"])


def load_static_providers(config_dir: Path | None = None) -> list[StaticProvider]:
    cfg_dir = config_dir or CONFIG_DIR
    providers = []
    for path in sorted((cfg_dir / "providers").glob("*.yaml")):
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        known = {f for f in StaticProvider.__dataclass_fields__ if f != "extra"}
        kwargs = {k: v for k, v in raw.items() if k in known}
        extra = {k: v for k, v in raw.items() if k not in known}
        lv = kwargs.get("last_verified")
        if lv is not None:
            kwargs["last_verified"] = str(lv)
        providers.append(StaticProvider(**kwargs, extra=extra))
    return providers
