"""Repair loop + durable skill state for the Blender AI agent.
# mypy: ignore-errors

Two cooperating subsystems, both self-contained in this module:

1. Repair loop: one loop instance per failing task, with the file
   contract ``<store>/loops/repair/``: ``LOOP.md`` (the loop contract,
   written once), ``state.md`` (current state; whole-file replace;
   statuses running|waiting|paused|completed|stopped) and ``record.md``
   (append-only; one entry per activation or tick reaching evaluation).
   Iteration flow: stop gates first, then continue gates, bounded
   actions, record entry, state update. Pause/stop beat continue; an
   activation bound caps automatic retries.

2. Skill state: durable notes with id/kind/content/created_at/updated_at/
   source/confidence/count, merged on normalized content keys (count+1,
   keep latest phrasing, bump confidence), bounded by eviction of the
   lowest-weight notes, persisted as JSON, and injected into the agent
   request as a bounded system message.

v0.1 boundary: classification is deterministic (error-signature
heuristics over three kinds: task_pattern, failure, environment) instead
of LLM-based, and the engine evaluates the loop contract's gates in
code. Allowed actions are the agent's existing tools; the loop drives
whole agent rounds (one bounded iteration = one agent run ending in a
final answer, an error, or a user stop).

This module must stay importable without bpy (pure stdlib) so the loop and
store logic is testable outside Blender.
"""

from __future__ import annotations

import json
import os
import re
import threading
import uuid
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, Literal

try:
    from . import debuglog
except ImportError:  # loaded standalone by the pure-python test harness
    debuglog = None

__all__ = [
    "LoopDecision",
    "Note",
    "RepairLoop",
    "SkillState",
    "SkillStore",
    "classify_error",
    "error_signature",
    "get_loop",
    "inject_block",
    "inject_into_request",
    "loop_dir",
    "on_round_failure",
    "on_round_success",
    "on_user_interrupt",
    "read_record_entries",
    "read_state_fields",
    "resolve_store_dir",
]

NoteKind = Literal["task_pattern", "failure", "environment"]
LoopStatus = Literal["running", "waiting", "paused", "completed", "stopped"]

MAX_NOTES: Final[int] = 50
INJECT_LIMIT: Final[int] = 8
SUMMARY_LIMIT: Final[int] = 160
DEFAULT_STORE: Final[str] = "BlenderAI/skills"
SKILL_MARK: Final[str] = "# Durable skill state"
REPAIR_MARK: Final[str] = "# Repair loop"
BASE_CONFIDENCE: Final[float] = 0.6
RESOLVED_CONFIDENCE: Final[float] = 0.8
CONFIDENCE_STEP: Final[float] = 0.1
CONFIDENCE_CAP: Final[float] = 0.95

STATE_FIELDS: Final[tuple[str, ...]] = (
    "Status",
    "Current focus",
    "Next action",
    "Blockers",
    "Continue conditions currently true",
    "Pause/stop conditions currently true",
    "Last direction check",
    "Pending human review",
    "Activation bound",
    "Activation bound progress",
    "Iteration count",
    "Updated at",
)

ENVIRONMENT_MARKERS: Final[tuple[str, ...]] = (
    "api key",
    "provider",
    "connection",
    "timeout",
    "online access",
    "ssl",
    "unreachable",
    "unauthorized",
    "rate limit",
)

# The shipped loop contract (written to LOOP.md on activation).
REPAIR_LOOP_MD: Final[str] = """# LOOP: repair

## Use For
Fixing agent rounds that ended in an error, by bounded automatic retries
that carry the failure signature forward as durable context.

## Trigger
An agent round surfaced an error result in the chat.

## Run Policy
Automatic, in the agent poll loop. One iteration = one agent round.
No background watcher; the loop advances only between rounds.

## Continue When
- The activation bound (preferences: Repair loop bound) is not reached.
- The last two failure signatures are not identical (still making progress).
- The user has not stopped the round.

## Stop When
- The activation bound is reached.
- The same failure signature repeats on consecutive failures (no progress).
- A round ends successfully (status completed).

## Allowed Actions
- Re-run agent rounds with the existing tools and skill-state injection.
- Append record entries and update state.md.
- Store failure/task_pattern notes in the skill store.

## Ask Human When
- Never automatically; the chat panel is the human channel. A user stop
  pauses the loop immediately.

## State To Track
Status, current focus, next action, blockers, continue/stop conditions,
direction check, pending human review, activation bound + progress,
iteration count, updated at (state.md, replace values, never append).

## Record Each Iteration
Iteration number, error signature, action taken, outcome
(record.md, append-only; one entry per activation or tick).

## Direction
Narrow: make the failed round succeed. No scope drift; a successful round
closes the loop.
"""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _short_id() -> str:
    return uuid.uuid4().hex[:12]


def error_signature(error_text: str) -> str:
    """First line of the error, trimmed — the loop's repeat-detection key."""
    stripped = error_text.strip()
    if not stripped:
        return "(empty error)"
    return stripped.splitlines()[0][:SUMMARY_LIMIT]


def classify_error(error_text: str) -> NoteKind:
    """Deterministic kind: provider/transport issues vs task failures."""
    lowered = error_text.lower()
    if any(marker in lowered for marker in ENVIRONMENT_MARKERS):
        return "environment"
    return "failure"


@dataclass(frozen=True, slots=True)
class Note:
    """One durable skill-state note."""

    id: str
    kind: NoteKind
    content: str
    created_at: str
    updated_at: str
    source: str = "system"
    confidence: float = BASE_CONFIDENCE
    count: int = 1

    @property
    def weight(self) -> float:
        return self.count * self.confidence


@dataclass(frozen=True, slots=True)
class SkillState:
    """Bounded note bank."""

    name: str = "blender-agent"
    scope: str = "blender scene building"
    notes: tuple[Note, ...] = ()
    version: int = 1
    updated_at: str = field(default_factory=_now)


class SkillStore:
    """JSON persistence for one SkillState (atomic write, thread-safe)."""

    __slots__ = ("_lock", "_path")

    def __init__(self, store_dir: str | os.PathLike[str]) -> None:
        self._path = Path(store_dir) / "skill_state.json"
        self._lock = threading.Lock()

    def load(self) -> SkillState:
        try:
            raw = self._path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return SkillState()
        try:
            data = json.loads(raw)
            notes = tuple(
                Note(
                    id=str(n["id"]),
                    kind=n["kind"],
                    content=str(n["content"]),
                    created_at=str(n["created_at"]),
                    updated_at=str(n["updated_at"]),
                    source=str(n.get("source", "system")),
                    confidence=float(n.get("confidence", BASE_CONFIDENCE)),
                    count=int(n.get("count", 1)),
                )
                for n in data.get("notes", ())
            )
        except (json.JSONDecodeError, KeyError, TypeError, ValueError, AttributeError) as exc:
            # Quarantine a corrupt store instead of crashing the caller:
            # load() runs inside the Send operator, with the user message
            # already added to the history.
            self._quarantine(exc)
            return SkillState()
        return SkillState(
            name=str(data.get("name", "blender-agent")),
            scope=str(data.get("scope", "blender scene building")),
            notes=notes,
            version=int(data.get("version", 1)),
            updated_at=str(data.get("updated_at", _now())),
        )

    def _quarantine(self, exc: Exception) -> None:
        """Move an unreadable store aside so the next save can start fresh."""
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        quarantine = self._path.with_name(f"skill_state.corrupt-{stamp}.json")
        if debuglog is not None:
            debuglog.warn(f"corrupt skill_state.json ({exc}); quarantined")
        try:
            self._path.replace(quarantine)
        except OSError:
            pass  # best-effort: the fresh default state still lets the chat work

    def save(self, state: SkillState) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "name": state.name,
            "scope": state.scope,
            "version": state.version,
            "updated_at": state.updated_at,
            "notes": [
                {
                    "id": n.id,
                    "kind": n.kind,
                    "content": n.content,
                    "created_at": n.created_at,
                    "updated_at": n.updated_at,
                    "source": n.source,
                    "confidence": n.confidence,
                    "count": n.count,
                }
                for n in state.notes
            ],
        }
        tmp = self._path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, self._path)

    def add_note(
        self,
        content: str,
        kind: NoteKind,
        source: str = "system",
        confidence: float = BASE_CONFIDENCE,
    ) -> Note:
        """Merge-on-key capture: same normalized content bumps count."""
        normalized = re.sub(r"\s+", " ", content).strip()
        if not normalized:
            raise ValueError("note content must not be empty")
        key = normalized.lower()
        with self._lock:
            state = self.load()
            existing = next((n for n in state.notes if re.sub(r"\s+", " ", n.content).strip().lower() == key), None)
            now = _now()
            if existing is not None:
                merged = Note(
                    id=existing.id,
                    kind=existing.kind,
                    content=normalized,
                    created_at=existing.created_at,
                    updated_at=now,
                    source=existing.source,
                    confidence=min(max(existing.confidence, confidence) + CONFIDENCE_STEP, CONFIDENCE_CAP),
                    count=existing.count + 1,
                )
                notes = tuple(merged if n is existing else n for n in state.notes)
            else:
                merged = Note(
                    id=_short_id(),
                    kind=kind,
                    content=normalized,
                    created_at=now,
                    updated_at=now,
                    source=source,
                    confidence=confidence,
                )
                notes = (*state.notes, merged)
            notes = tuple(sorted(notes, key=lambda n: n.weight, reverse=True)[:MAX_NOTES])
            self.save(replace(state, notes=notes, updated_at=now))
            return merged


@dataclass(frozen=True, slots=True)
class LoopDecision:
    """What the loop decided after one tick (agent polls this)."""

    action: Literal["idle", "continue", "stop"]
    iteration: int = 0
    bound: int = 0
    reason: str = ""


class RepairLoop:
    """In-process driver for one repair loop instance.

    Files live under ``<store_dir>/loops/repair/``: LOOP.md (contract,
    written once), state.md (replaced whole on every update) and record.md
    (append-only). Stop gates run before continue gates; pause/stop beat
    continue.
    """

    __slots__ = ("_active", "_bound", "_goal", "_iteration", "_last_sig", "_lock", "_store_dir")

    def __init__(self, store_dir: str | os.PathLike[str]) -> None:
        self._store_dir = Path(store_dir)
        self._lock = threading.Lock()
        self._active: bool = False
        self._iteration: int = 0
        self._bound: int = 0
        self._goal: str = ""
        self._last_sig: str = ""

    @property
    def active(self) -> bool:
        return self._active

    @property
    def goal(self) -> str:
        """The failure signature this loop instance is repairing."""
        return self._goal

    @property
    def iteration(self) -> int:
        return self._iteration

    @property
    def bound(self) -> int:
        return self._bound

    def _loop_dir(self) -> Path:
        return loop_dir(self._store_dir)

    def _contract_path(self) -> Path:
        return self._loop_dir() / "LOOP.md"

    def _state_path(self) -> Path:
        return self._loop_dir() / "state.md"

    def _record_path(self) -> Path:
        return self._loop_dir() / "record.md"

    # ---------------------------------------------------------------- files
    def _ensure_contract(self) -> None:
        path = self._contract_path()
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(REPAIR_LOOP_MD, encoding="utf-8")

    @staticmethod
    def _write_state(path: Path, fields: dict[str, str]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = ["# Loop State", ""]
        lines.extend(f"{name}: {fields.get(name, '')}" for name in STATE_FIELDS)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def _append_record(self, body_lines: list[str]) -> None:
        path = self._record_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        entry = "\n".join(body_lines)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(f"\n## {entry}\n")

    def _record_state(self, status: LoopStatus, focus: str, next_action: str,
                      stop_cond: str, bound: int, iteration: int) -> None:
        self._write_state(
            self._state_path(),
            {
                "Status": status,
                "Current focus": focus,
                "Next action": next_action,
                "Blockers": "none",
                "Continue conditions currently true": (
                    f"activation bound not reached ({iteration}/{bound})"
                    if status == "running" else "none"
                ),
                "Pause/stop conditions currently true": stop_cond,
                "Last direction check": f"in scope: repair of the failing round ({focus[:80]})",
                "Pending human review": "none",
                "Activation bound": str(bound),
                "Activation bound progress": f"{iteration}/{bound}",
                "Iteration count": str(iteration),
                "Updated at": _now(),
            },
        )

    # ---------------------------------------------------------------- ticks
    def _activate(self, goal: str, bound: int) -> None:
        self._ensure_contract()
        now = _now()
        self._append_record([
            "Activation",
            "Loop: repair",
            f"Activated at: {now}",
            "Activated by: agent round (automatic policy)",
            "Confirmation: errored round in the agent chat",
            f"Initial summary: {goal}",
        ])
        self._active = True
        self._iteration = 1
        self._bound = max(1, bound)
        self._goal = goal
        self._record_state(
            "running", goal, "retry the round with the failure signature in context",
            "none", self._bound, 1,
        )

    def _stop_locked(self, sig: str, stop_cond: str) -> LoopDecision:
        """No-action record + stopped state; caller holds the lock."""
        reason = f"Stop When true: {stop_cond}"
        self._append_record([
            "No-action",
            f"Iteration: {self._iteration}",
            f"Error signature: {sig}",
            f"Reason: {reason}",
            "Action: none",
            "Outcome: stopped",
        ])
        self._record_state(
            "stopped", self._goal, "none",
            stop_cond, self._bound, self._iteration,
        )
        self._active = False
        return LoopDecision("stop", self._iteration, self._bound, reason)

    def on_failure(self, error_text: str, bound: int) -> LoopDecision:
        """Tick on a failed round: gate, record, decide continue/stop.

        Iteration counts repair rounds that run (the initial error activates
        iteration 1). Continue only while the bound is not exhausted and the
        signature is not repeating (stop gates before continue gates).
        """
        sig = error_signature(error_text)
        with self._lock:
            if not self._active:
                self._activate(sig, max(1, bound))
            elif self._iteration >= self._bound:
                return self._stop_locked(sig, "activation bound reached")
            elif self._last_sig == sig:
                return self._stop_locked(
                    sig, "identical failure signature repeated (no progress)")
            else:
                self._iteration += 1

            self._append_record([
                f"Iteration {self._iteration}",
                f"Error signature: {sig}",
                "Action: retry with failure signature in context",
                "Outcome: pending",
            ])
            if self._iteration > 1:
                self._record_state(
                    "running", self._goal,
                    "retry the round with the failure signature in context",
                    "none", self._bound, self._iteration,
                )
            self._last_sig = sig
            return LoopDecision("continue", self._iteration, self._bound, "")

    def on_success(self, summary: str) -> LoopDecision:
        """Close the loop on a successful round (status completed)."""
        with self._lock:
            if not self._active:
                return LoopDecision("idle")
            self._append_record([
                f"Iteration {self._iteration}",
                "Error signature: (none this round)",
                "Action: none — round succeeded",
                f"Outcome: resolved — {summary[:SUMMARY_LIMIT]}",
            ])
            self._record_state(
                "completed", self._goal, "none",
                "stop condition met: round succeeded", self._bound, self._iteration,
            )
            self._active = False
            return LoopDecision(
                "stop", self._iteration, self._bound,
                f"loop completed: {self._goal[:SUMMARY_LIMIT]}",
            )

    def on_interrupt(self) -> None:
        """User stop pauses the loop (paused must not auto-run)."""
        with self._lock:
            if not self._active:
                return
            self._append_record([
                "No-action",
                "Reason: user stopped the round",
                "Action: none",
                "Outcome: paused",
            ])
            self._record_state(
                "paused", self._goal, "none",
                "paused by user", self._bound, self._iteration,
            )
            self._active = False

    def status_line(self) -> str:
        """One-line panel summary ('' when idle)."""
        with self._lock:
            if not self._active:
                return ""
            return f"Repair loop: iteration {self._iteration}/{self._bound} — {self._goal}"


# ------------------------------------------------------------------ singleton

_LOOP: RepairLoop | None = None
_LOOP_LOCK = threading.Lock()


def loop_dir(store_dir: str | os.PathLike[str]) -> Path:
    """Directory holding the repair loop's LOOP.md/state.md/record.md."""
    return Path(store_dir) / "loops" / "repair"


def read_state_fields(store_dir: str | os.PathLike[str]) -> dict[str, str]:
    """Parse state.md into ``{field: value}`` ({} when no loop has run).

    Lines are ``Field: value`` under a ``# Loop State`` header — the exact
    shape RepairLoop._write_state produces.
    """
    path = loop_dir(store_dir) / "state.md"
    try:
        with path.open(encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    except FileNotFoundError:
        return {}
    fields: dict[str, str] = {}
    for line in lines:
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition(": ")
        if sep:
            fields[key] = value
    return fields


def read_record_entries(
    store_dir: str | os.PathLike[str], last_n: int = 6,
) -> list[tuple[str, list[str]]]:
    """Last ``last_n`` record.md entries as ``(header, body_lines)``.

    record.md is append-only with ``## `` entry headers; body lines are the
    non-header lines of each entry, blank lines dropped.
    """
    path = loop_dir(store_dir) / "record.md"
    try:
        with path.open(encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    except FileNotFoundError:
        return []
    entries: list[tuple[str, list[str]]] = []
    for line in lines:
        if line.startswith("## "):
            entries.append((line[3:].strip(), []))
        elif entries and line.strip():
            entries[-1][1].append(line.strip())
    return entries[-max(1, last_n):]


def get_loop(store_dir: str | os.PathLike[str]) -> RepairLoop:
    """Process-wide loop instance; rebuilds if the store dir changed."""
    global _LOOP
    with _LOOP_LOCK:
        if _LOOP is None or _LOOP._store_dir != Path(store_dir):
            _LOOP = RepairLoop(store_dir)
        return _LOOP


def resolve_store_dir(configured: str) -> Path:
    """Preferences dir, or a home-relative fallback when unset."""
    trimmed = configured.strip()
    return Path(trimmed) if trimmed else Path.home() / DEFAULT_STORE


# ------------------------------------------------------- agent-facing facade

def on_round_failure(error_text: str, bound: int, store_dir: str) -> LoopDecision:
    loop = get_loop(resolve_store_dir(store_dir))
    # No durable note here: round failures at this point are provider or
    # transport errors (401/timeout/5xx) — recording their first line used
    # to permanently pollute the injected note bank for every future chat.
    return loop.on_failure(error_text, bound)


def on_round_success(summary: str, store_dir: str) -> LoopDecision:
    loop = get_loop(resolve_store_dir(store_dir))
    decision = loop.on_success(summary)
    if decision.action == "stop" and decision.reason.startswith("loop completed"):
        store = SkillStore(resolve_store_dir(store_dir))
        store.add_note(
            f"{loop.goal} — fixed by: {summary[:SUMMARY_LIMIT]}",
            "task_pattern",
            confidence=RESOLVED_CONFIDENCE,
        )
    return decision


def on_user_interrupt(store_dir: str) -> None:
    get_loop(resolve_store_dir(store_dir)).on_interrupt()


def inject_block(store_dir: str) -> str:
    """Bounded durable-knowledge block for the next request ('' when empty)."""
    store = SkillStore(resolve_store_dir(store_dir))
    notes = sorted(store.load().notes, key=lambda n: n.weight, reverse=True)[:INJECT_LIMIT]
    if not notes:
        return ""
    lines = [SKILL_MARK, "Learned from earlier sessions (bounded, merged):"]
    lines.extend(f"- [{n.kind}] {n.content} (x{n.count})" for n in notes)
    return "\n".join(lines)


def inject_into_request(messages: list[dict], store_dir: str) -> None:
    """Attach the durable-knowledge block to a request's messages in place.

    Idempotent: the previously injected system message (found by marker) is
    replaced or removed, so repeated rounds never stack duplicates. The
    caller passes a request-only snapshot (history.outgoing_snapshot), so
    the block is never persisted into the .blend nor truncated later by
    history.trim() — it rides every request, and only requests.
    While the repair loop is active, the block also carries the loop's
    focus (the failure signature) and instructs a changed approach — the
    loop's bounded action for this iteration.
    """
    loop = get_loop(resolve_store_dir(store_dir))
    parts: list[str] = []
    block = inject_block(store_dir)
    if block:
        parts.append(block)
    if loop.active:
        parts.append(
            f"{REPAIR_MARK}\n"
            f"Automatic repair loop iteration {loop.iteration}/{loop.bound}. "
            f"Failing round: {loop.goal}\n"
            "Do NOT repeat the same failing approach: change the strategy "
            "for this attempt."
        )

    index: int | None = None
    for i, message in enumerate(messages):
        content = str(message.get("content", ""))
        if message.get("role") == "system" and (
            SKILL_MARK in content or REPAIR_MARK in content
        ):
            index = i
            break
    if not parts:
        if index is not None:
            del messages[index]
        return
    injected = {
        "role": "system",
        "content": "\n\n".join(parts)
        + "\n\nApply this durable knowledge when relevant; ignore anything that "
        "contradicts the user's current request.",
    }
    if index is not None:
        messages[index] = injected
        return
    # After any leading system messages (the tool-guidance prompt), else at 0.
    insert_at = 0
    while insert_at < len(messages) and messages[insert_at].get("role") == "system":
        insert_at += 1
    messages.insert(insert_at, injected)
