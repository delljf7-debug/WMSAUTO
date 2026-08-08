# Agent Memory & Grounding Architecture

**Status:** Design proposal
**Scope:** How a long-running (24/7) agent operating inside WMSAUTO handles memory and
context such that it does not produce confident wrong data or issue wrong tasks to humans.

---

## 1. The problem is not memory size

"Memory/context issues" and "confidently wrong output" look like two problems. They are one.

An agent whose context has degraded **does not know that it degraded**. Model confidence is
generated from the fluency of its own reasoning, not from the completeness of its inputs.
Context loss is therefore silent, and:

```
silent context loss + fluent generation = confident wrong output
```

This is why the reflexive fix — a bigger memory, a vector store, more retrieval of the
agent's own history — makes the failure worse rather than better. A larger memory of the
agent's own prior statements produces an agent that is more *consistently* wrong, and more
persuasive about it.

In a warehouse the blast radius is physical. A wrong pick task, a wrong putaway location, a
wrong cycle-count adjustment, or a wrong replenishment is executed by a person. Once the
inventory record drifts from physical reality, every downstream decision is poisoned, and
the agent cannot detect the drift because the record *is* its only view of reality.

## 2. Organizing principle

> **Memory holds *how*, not *what*.**
>
> Procedures, site quirks, and pointers persist.
> Facts do not — they are re-derived from the system of record at the moment of use.

Corollary: **facts have TTLs, procedures do not.** Inventory position is true for seconds.
A putaway rule is true for months. The moment those two live in the same store under the
same trust level, the system will confidently assert stale inventory as current.

## 3. State layers

Four layers with deliberately unequal trust. Nothing is promoted between layers implicitly.

| Layer | Contents | Lifetime | Trust |
|---|---|---|---|
| **System of record** (WMS DB) | inventory, locations, orders, tasks | authoritative | Only source of fact. Never cached across a decision. |
| **Working context** (the turn) | current reasoning, retrieved rows | ephemeral | Assume lossy and summarizable. No consequential fact lives *only* here. |
| **Episodic log** (append-only) | `task_issued`, `task_confirmed`, `exception_raised`, `human_override` — with IDs | permanent | Audit and reconstruction. Not a reasoning shortcut. |
| **Procedural memory** | learned rules, site quirks, escalation contacts | slow, versioned, expiring | Human-reviewed before promotion. |

Reads from the system of record are timestamped and version-stamped (`read_at`, etag/rowver)
so that staleness is a measurable quantity rather than an assumption.

## 4. Making confidence structural, not introspective

A model cannot be prompted into calibration. Do not rely on it reporting its own
uncertainty. Enforce these in code instead.

### 4.1 No assertion without provenance

Every fact the agent emits carries a provenance triple:

```
(source, record_id, read_at)
```

A claim with no provenance token cannot be serialized into output. This is a type-system
constraint at the boundary, not a prompt instruction — prompt instructions degrade under
context pressure, types do not.

### 4.2 Every instruction must be checkable by physical reality

Tasks issued to humans are phrased so that executing them verifies them. If the agent
says *"pick 12 of SKU-A from A-12-04"* and the scan at A-12-04 returns SKU-B, the error is
caught by the world, not by the model. Scan-to-confirm must not be bypassable.

### 4.3 Two-source gate on irreversible operations

Inventory adjustments, shipment release, and anything that moves atoms or money requires
agreement between two independent derivations (e.g. WMS record plus a recent physical scan
event). Disagreement escalates to a human; it does not resolve to the more recent value.

### 4.4 Abstention is a first-class output

`INSUFFICIENT_EVIDENCE` is a valid, expected, logged, and unpenalized result with a defined
handoff path. Without a modeled abstention, the agent will always produce *something*.

Monitor the abstention rate. A rate of exactly zero across a week is an alarm, not a
success.

## 5. What 24/7 operation adds

Continuous runtime introduces failure modes that a one-shot invocation does not have.

### 5.1 Drift — bounded episodes

The agent does not run "forever." It runs bounded **episodes** (a shift, an hour, a task
batch). Each episode re-derives its world from the system of record on start. Nothing
crosses an episode boundary except the durable event log and reviewed procedural memory.

Deliberate amnesia at the boundary is a feature. It is the only reliable way to stop
assumption accumulation.

### 5.2 Compaction lies

When a long context is summarized, the summary is a lossy, model-authored artifact that
subsequently reads as authoritative source material.

Mitigations:
- Summaries are explicitly marked as summaries, never as facts.
- Consequential identifiers are carried forward as structured fields, not prose.
- After any compaction, re-read the system of record for anything about to be acted on.

### 5.3 Staleness and clock state

Every episode begins by re-establishing current time, current shift, open exceptions, and
the freshness of the data it is about to use. This is cheap; run it unconditionally.

### 5.4 External watchdog

A long-running agent cannot observe its own degradation. A separate process monitors
liveness *and* sanity: output rate, abstention rate, and spot-checks of recent assertions
against the system of record.

## 6. Operating inside a human environment

- **Trust decay is the primary human hazard.** If the agent is right 500 times, nobody
  checks the 501st. Verification must not erode with demonstrated accuracy: mandatory scan
  confirmation, rotating spot-checks, sampled audits.
- **Dissent halts the batch, not just the task.** A worker reporting "this doesn't match
  what I see" is the highest-value signal in the system and must be routed as such.
- **Never issue a task a human cannot safely refuse.** Every instruction states what it is
  based on and offers a one-tap mismatch path.
- **Phrasing carries certainty.** "WMS shows 12 units at A-12-04 as of 14:32" survives
  being wrong. "There are 12 units at A-12-04" destroys trust when it is wrong.

## 7. Build order

1. Provenance-typed fact objects and the no-assertion-without-citation boundary.
2. Append-only event log with stable IDs.
3. Episode boundaries and re-derivation on episode start.
4. Abstention path and escalation routing.
5. Two-source gate on irreversible operations.
6. External watchdog and metrics.
7. Procedural memory, behind a human review gate.

The "memory system" is deliberately last and is the smallest component.

## 8. Metrics that detect confident wrongness

| Metric | Why it matters |
|---|---|
| Human override rate (and trend) | Direct measure of disagreement with reality |
| Contradiction rate | Agent assertion vs. the next physical scan |
| Staleness at decision time (p50/p99) | Age of the data a decision was actually based on |
| Abstention rate | Must be nonzero and stable |
| **Time-to-detection** of a bad assertion | The one that matters most — not all errors are preventable |

## 9. Honest limit

"Never wrong" is not achievable and should not be claimed. The achievable and correct goal:

> **Wrong output cannot become a confident, unverifiable, irreversible action.**

The objective is not a perfect model. It is a system in which model error is structurally
caught — by provenance, by physical confirmation, by episode boundaries, by a second
source, and by a human who is still expected to look — before it costs anything.
