"""External signal layer (§5).

Facts the system of record structurally cannot hold: carrier disruption,
weather, customs, recalls, regulatory change. Retrieved via Groq compound,
whose `executed_tools[].search_results[]` returns source URLs — which is the
only reason this layer can satisfy the provenance rule at all (§5.1).

`ExternalSignal` is deliberately **not** a `Fact` and shares no base class with
one. That separation is the enforcement of §5.3 r1: a signal cannot be passed to
`gates.two_source` because it is not the right type, and if someone works around
that by wrapping it as a `Fact[EXTERNAL_SIGNAL]`, the gate rejects it at runtime.
Two independent barriers, because this is the lowest-trust input in the system.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum

from .decision import Escalate

#: Restrict compound to retrieval only; code execution and Wolfram are not
#: needed here and widen the surface (§5.3 r5).
ENABLED_TOOLS = ("web_search", "visit_website")

#: Pin the dated version rather than `latest` (§5.3 r6) — retrieval behaviour
#: must not drift silently under a long-running agent.
COMPOUND_MODEL = "groq/compound-mini"
COMPOUND_VERSION = "2025-08-16"


class TriggerKind(Enum):
    """Standing triggers from §5.2. Retrieval fires only on a match."""

    CARRIER_DISRUPTION = "carrier_disruption"
    SEVERE_WEATHER = "severe_weather"
    PORT_OR_CUSTOMS_CLOSURE = "port_or_customs_closure"
    SUPPLIER_RECALL = "supplier_recall"
    REGULATORY_CHANGE = "regulatory_change"
    SCHEDULE_CHANGE = "schedule_change"

    @property
    def default_ttl(self) -> timedelta:
        """Signals expire (§5.3 r7). A carrier outage is true for hours, not days."""
        return {
            TriggerKind.CARRIER_DISRUPTION: timedelta(hours=6),
            TriggerKind.SEVERE_WEATHER: timedelta(hours=12),
            TriggerKind.PORT_OR_CUSTOMS_CLOSURE: timedelta(hours=24),
            TriggerKind.SUPPLIER_RECALL: timedelta(days=7),
            TriggerKind.REGULATORY_CHANGE: timedelta(days=30),
            TriggerKind.SCHEDULE_CHANGE: timedelta(days=7),
        }[self]


@dataclass(frozen=True, slots=True)
class SignalSource:
    """One row of `executed_tools[].search_results[]` (§5.1)."""

    url: str
    title: str
    score: float
    retrieved_at: datetime

    def __post_init__(self) -> None:
        if not self.url:
            # §5.3 r3 — a signal that cannot cite a URL does not exist.
            raise ValueError("a signal source without a URL is not a source")


@dataclass(frozen=True, slots=True)
class ExternalSignal:
    """A flag raised for human review. Never an input to an action."""

    kind: TriggerKind
    summary: str
    sources: tuple[SignalSource, ...]
    retrieved_at: datetime
    expires_at: datetime
    quarantined: bool = field(default=False)

    def __post_init__(self) -> None:
        if not self.sources:
            raise ValueError("an uncited signal cannot be raised (§5.3 r3)")

    def is_expired(self, now: datetime) -> bool:
        return now >= self.expires_at

    def to_escalation(self) -> Escalate:
        """The only exit from this layer: a human decides (§5.4)."""
        return Escalate(
            detail=f"[{self.kind.value}] {self.summary}",
            sources=tuple(s.url for s in self.sources),
        )


# Heuristic only. Defence in depth, not a solution — the load-bearing control
# is that retrieved text is never placed where instructions are read from.
_DIRECTIVE_PATTERNS = (
    r"ignore (all |any )?(previous|prior|above)",
    r"disregard (the |your )?(above|instructions|rules)",
    r"you (are|must|should) now",
    r"new instructions?:",
    r"system prompt",
    r"</?(system|instructions?)>",
)
_DIRECTIVE_RE = re.compile("|".join(_DIRECTIVE_PATTERNS), re.IGNORECASE)


def looks_like_injection(text: str) -> bool:
    """Flag retrieved content that reads as a directive rather than data (§5.3 r2)."""
    return bool(_DIRECTIVE_RE.search(text))


def search_settings(allowlist: tuple[str, ...], country: str = "united states") -> dict:
    """Build the compound `search_settings` payload (§5.3 r4).

    The allowlist is what raises open-web retrieval from unusable to merely
    low-trust. It is site-specific and must name real carriers, port
    authorities, and regulators — not a generic wildcard.
    """
    if not allowlist:
        raise ValueError("refusing to retrieve without a domain allowlist (§5.3 r4)")
    return {"include_domains": list(allowlist), "country": country}


def should_retrieve(depends_on_external_state: bool, would_change_action: bool) -> bool:
    """The §5.2 trigger predicate. Both clauses must hold.

    Evaluated on every decision; fires rarely. Retrieval volume is injection
    surface, so the default is not to retrieve.
    """
    return depends_on_external_state and would_change_action
