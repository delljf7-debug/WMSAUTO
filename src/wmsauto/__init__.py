"""WMSAUTO — grounded agent primitives for warehouse operations.

Implements docs/agent-memory-and-grounding.md. The organizing constraint: a
wrong model output cannot become a confident, unverifiable, irreversible action.
"""

from .decision import Abstain, AbstainReason, Act, Decision, Escalate, is_action
from .events import Event, EventLog, EventType
from .gates import single_source, two_source
from .provenance import Fact, MissingProvenance, Provenance, SourceKind, read
from .signals import ExternalSignal, SignalSource, TriggerKind, should_retrieve

__all__ = [
    "Abstain",
    "AbstainReason",
    "Act",
    "Decision",
    "Escalate",
    "Event",
    "EventLog",
    "EventType",
    "ExternalSignal",
    "Fact",
    "MissingProvenance",
    "Provenance",
    "SignalSource",
    "SourceKind",
    "TriggerKind",
    "is_action",
    "read",
    "should_retrieve",
    "single_source",
    "two_source",
]
