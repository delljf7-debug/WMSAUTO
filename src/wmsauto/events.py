"""Append-only episodic log (§3).

Records what the agent did and observed, with stable IDs. This is the audit and
reconstruction substrate — deliberately not a reasoning shortcut, and
deliberately not summarised. There is no update or delete path.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Iterator, Mapping


class EventType(Enum):
    EPISODE_STARTED = "episode_started"
    EPISODE_ENDED = "episode_ended"
    TASK_ISSUED = "task_issued"
    TASK_CONFIRMED = "task_confirmed"
    TASK_REFUSED = "task_refused"
    EXCEPTION_RAISED = "exception_raised"
    HUMAN_OVERRIDE = "human_override"
    ABSTAINED = "abstained"
    ESCALATED = "escalated"
    SIGNAL_RAISED = "signal_raised"
    INJECTION_SUSPECTED = "injection_suspected"


@dataclass(frozen=True, slots=True)
class Event:
    seq: int
    event_type: EventType
    occurred_at: datetime
    episode_id: str
    payload: Mapping[str, Any] = field(default_factory=dict)
    citations: tuple[str, ...] = field(default=())


class AppendOnlyViolation(Exception):
    """Raised on any attempt to mutate recorded history."""


class EventLog:
    """In-memory reference implementation. Swap for a durable store in §8 step 2."""

    def __init__(self) -> None:
        self._events: list[Event] = []

    def append(
        self,
        event_type: EventType,
        *,
        occurred_at: datetime,
        episode_id: str,
        payload: Mapping[str, Any] | None = None,
        citations: tuple[str, ...] = (),
    ) -> Event:
        event = Event(
            seq=len(self._events),
            event_type=event_type,
            occurred_at=occurred_at,
            episode_id=episode_id,
            payload=dict(payload or {}),
            citations=citations,
        )
        self._events.append(event)
        return event

    def __iter__(self) -> Iterator[Event]:
        return iter(tuple(self._events))

    def __len__(self) -> int:
        return len(self._events)

    def __getitem__(self, index: int) -> Event:
        return self._events[index]

    def __setitem__(self, index: int, value: Event) -> None:
        raise AppendOnlyViolation("the episodic log is append-only")

    def __delitem__(self, index: int) -> None:
        raise AppendOnlyViolation("the episodic log is append-only")

    def of_type(self, event_type: EventType) -> tuple[Event, ...]:
        return tuple(e for e in self._events if e.event_type is event_type)

    def for_episode(self, episode_id: str) -> tuple[Event, ...]:
        return tuple(e for e in self._events if e.episode_id == episode_id)
