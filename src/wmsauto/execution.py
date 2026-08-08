"""Gated command execution.

Shell access is the largest hole in this architecture. Every gate in
`wmsauto.gates` is bypassed by one unconstrained `subprocess` call, so execution
is placed *inside* the grounding rules rather than around them:

* A command that **reads** yields a `Fact` carrying the command as provenance.
  Output that never becomes a fact cannot ground anything downstream.
* A command that **mutates** requires an `Act` — which can only be obtained by
  passing `gates.two_source`. The authorization is an unforgeable capability:
  there is no path to a mutation that did not clear the gate first.
* Every invocation, including every refusal, lands in the episodic log.

Commands are executed without a shell (`shell=False`, argv list). String
interpolation into a command is the same class of defect as placing retrieved
web text in an instruction position (§5.3 r2) — never build argv from untrusted
input.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from typing import Sequence

from .decision import Abstain, AbstainReason, Act, Decision
from .events import EventLog, EventType
from .provenance import Fact, SourceKind, read


class CommandRefused(Exception):
    """Raised when a command is not permitted by policy."""


@dataclass(frozen=True, slots=True)
class CommandPolicy:
    """Allowlist for executable programs.

    Deny-by-default. The allowlist names programs, never full command strings,
    so it cannot be widened by argument smuggling.
    """

    allowed: frozenset[str] = field(default=frozenset())

    def permits(self, argv: Sequence[str]) -> bool:
        return bool(argv) and argv[0] in self.allowed

    def check(self, argv: Sequence[str]) -> None:
        if not self.permits(argv):
            program = argv[0] if argv else "<empty>"
            raise CommandRefused(
                f"{program!r} is not on the command allowlist; "
                "execution is deny-by-default"
            )


@dataclass(frozen=True, slots=True)
class ShellResult:
    argv: tuple[str, ...]
    exit_code: int
    stdout: str
    stderr: str
    ran_at: datetime

    @property
    def succeeded(self) -> bool:
        return self.exit_code == 0

    def command(self) -> str:
        return " ".join(self.argv)


def _run(argv: Sequence[str], *, now: datetime, timeout: float) -> ShellResult:
    completed = subprocess.run(  # noqa: S603 - argv list, shell=False by construction
        list(argv),
        capture_output=True,
        text=True,
        timeout=timeout,
        shell=False,
    )
    return ShellResult(
        argv=tuple(argv),
        exit_code=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
        ran_at=now,
    )


def read_via_command(
    argv: Sequence[str],
    *,
    policy: CommandPolicy,
    log: EventLog,
    episode_id: str,
    now: datetime,
    source_kind: SourceKind = SourceKind.DERIVED,
    source_id: str | None = None,
    timeout: float = 30.0,
) -> Decision[str]:
    """Run a read-only command and return its output as a provenance-carrying fact.

    A non-zero exit is an abstention, not an empty string. Silently treating a
    failed command as "no results" is exactly the confident-wrongness path this
    codebase exists to close.
    """
    try:
        policy.check(argv)
    except CommandRefused as exc:
        log.append(
            EventType.EXCEPTION_RAISED,
            occurred_at=now,
            episode_id=episode_id,
            payload={"argv": list(argv), "error": str(exc)},
        )
        raise

    result = _run(argv, now=now, timeout=timeout)
    log.append(
        EventType.TASK_CONFIRMED if result.succeeded else EventType.EXCEPTION_RAISED,
        occurred_at=now,
        episode_id=episode_id,
        payload={
            "argv": list(result.argv),
            "exit_code": result.exit_code,
            "mode": "read",
        },
    )

    if not result.succeeded:
        return Abstain(
            reason=AbstainReason.INSUFFICIENT_SOURCES,
            detail=(
                f"{result.command()!r} exited {result.exit_code}: "
                f"{result.stderr.strip()[:200]}"
            ),
        )

    fact = read(
        result.stdout,
        source_kind=source_kind,
        source_id=source_id or result.argv[0],
        record_id=result.command(),
        read_at=now,
    )
    return Act(value=fact.value, grounds=(fact,))


def mutate_via_command(
    argv: Sequence[str],
    *,
    authorization: Act,
    policy: CommandPolicy,
    log: EventLog,
    episode_id: str,
    now: datetime,
    timeout: float = 30.0,
) -> ShellResult:
    """Run a state-changing command. Requires a gate-issued authorization.

    `authorization` is an `Act`, obtainable only from `gates.two_source` or
    `gates.single_source`. Requiring it by type means an irreversible operation
    cannot be reached without corroborated, fresh, grounded evidence — the gate
    is not a convention a caller can forget to apply.
    """
    if not isinstance(authorization, Act):
        raise CommandRefused(
            "a mutation requires an Act from a gate; "
            f"received {type(authorization).__name__}"
        )
    if not authorization.grounds:
        raise CommandRefused("authorization carries no grounds; refusing to mutate")

    policy.check(argv)

    result = _run(argv, now=now, timeout=timeout)
    log.append(
        EventType.TASK_ISSUED,
        occurred_at=now,
        episode_id=episode_id,
        payload={
            "argv": list(result.argv),
            "exit_code": result.exit_code,
            "mode": "mutate",
        },
        citations=tuple(authorization.citations()),
    )
    return result
