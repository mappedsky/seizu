"""What was already done, so nothing does it twice.

``sandbox__delegate`` opens a fresh ``create_react_agent`` per call, so
delegation N+1 knows nothing about N. One observed chat step made 136
delegations and, inside them, 678 ``graph__query`` and 73 ``graph__schema``
calls to answer a question about eight named CVEs -- it re-derived the same
ground over and over because nothing carried across the boundary. That step
spent 79,928 of the run's 96,000 spendable tokens and starved every step after
it.

This is the missing carry, at two scopes.

:class:`EpisodeLog` is the within-step one: an append-only, bounded record of
each sub-agent's task and outcome, replayed into the next sub-agent's prompt.
Scope is by construction, not by key -- a log is created for one step, held in a
:class:`~contextvars.ContextVar`, and discarded when the step ends. There is no
registry and no key, so there is no key to get wrong. Parallel steps get
independent logs (``asyncio.gather`` copies the context per task); delegations
inside one step share the object by reference.

:class:`SessionLedger` is the across-turn one. Turn-scoped memory left the same
hole one turn out: a follow-up question re-ran the queries the previous turn had
already run, on top of its own work, because nothing said they had been run. The
ledger holds the same episodes plus **receipts** -- the files earlier turns left
in the sandbox, which now survives between turns (see
:mod:`reporting.services.sandbox_session`) -- so a later turn can read data
rather than re-fetch it. It rides in the thread's LangGraph checkpoint, which is
already namespaced per user, and is handed back through
:meth:`SessionLedger.to_state`.

A receipt is only worth replaying while the file it names still exists, so each
records the sandbox it was written in and is rendered only for that sandbox. A
turn whose resume failed gets a new sandbox id, and every earlier receipt
silently stops being offered -- which is the correct answer, not a special case.
"""

from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

from reporting import settings
from reporting.services.untrusted import fence_overhead, fenced_within

# Below this an entry is a stub rather than a recollection, so drop it instead.
_MIN_ENTRY_CHARS = 120
# Share of a recall budget that receipts may take before episodes get the rest.
# They are pointers rather than content -- a few dozen characters each -- so a
# minority share still lists everything an ordinary session accumulates.
_RECEIPT_BUDGET_SHARE = 0.4
# Receipt fields are descriptions of data, not the data; cap them so one verbose
# Cypher query cannot crowd out the rest of the manifest.
_RECEIPT_PURPOSE_MAX = 220
_RECEIPT_COLUMNS_MAX = 12


@dataclass(frozen=True)
class Episode:
    """One sub-agent's assignment and what it reported back."""

    task: str
    outcome: str
    turn: int = 0


@dataclass(frozen=True)
class Receipt:
    """A file an earlier delegation left in the session's sandbox.

    The point is not the file but that the *data is already fetched*: a later
    turn that knows a query's rows are sitting in a JSON file reads them instead
    of re-running the query, which is the single most expensive thing a
    follow-up turn was doing.
    """

    path: str
    source: str
    purpose: str
    sandbox_id: str
    turn: int = 0
    rows: int | None = None
    columns: list[str] = field(default_factory=list)

    def render(self) -> str:
        shape = f"{self.rows} rows" if self.rows is not None else "data"
        if self.columns:
            shape += " of " + ", ".join(self.columns[:_RECEIPT_COLUMNS_MAX])
        line = f"- {self.path} ({shape}, from {self.source} on turn {self.turn})"
        return f"{line}: {self.purpose}" if self.purpose else line


def _within(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _stored_entry_max() -> int:
    """Longest an episode may be *stored*, derived rather than configured.

    A sub-agent result is capped at ``SANDBOX_MAX_OUTPUT_BYTES`` — 50KB by
    default — and the ledger keeps dozens of them in a checkpoint written on
    every turn, so storing them whole would put megabytes into the thread's
    state. Nothing is lost by cutting here: rendering already truncates to the
    largest budget an entry could ever be rendered into, so anything past that
    was only ever going to be dropped, one turn later, after being persisted.
    """
    return max(settings.CHAT_EPISODIC_RECALL_MAX_CHARS, settings.CHAT_SESSION_MEMORY_DIGEST_MAX_CHARS, _MIN_ENTRY_CHARS)


def _render_episodes(episodes: list[Episode], budget: int, *, numbered_from: int = 1) -> list[str]:
    """Newest first, within ``budget``; returns the entries in oldest-first order."""
    kept: list[str] = []
    remaining = budget
    for index, episode in enumerate(reversed(episodes), start=numbered_from):
        if remaining < _MIN_ENTRY_CHARS:
            break
        entry = _within(f"{index}. Task: {episode.task}\n   Result: {episode.outcome}", remaining)
        kept.append(entry)
        remaining -= len(entry) + 2
    kept.reverse()
    return kept


class SessionLedger:
    """What a conversation has already gathered, carried between its turns.

    Bounded on both axes and serialized into the thread checkpoint, so it grows
    with the conversation rather than with the work inside a turn.
    """

    def __init__(
        self,
        *,
        episodes: list[Episode] | None = None,
        receipts: list[Receipt] | None = None,
        turn: int = 1,
        max_entries: int | None = None,
        max_receipts: int | None = None,
    ) -> None:
        self._max_entries = settings.CHAT_SESSION_MEMORY_MAX_ENTRIES if max_entries is None else max_entries
        self._max_receipts = settings.CHAT_SESSION_MEMORY_MAX_RECEIPTS if max_receipts is None else max_receipts
        self._episodes: list[Episode] = list(episodes or [])
        self._receipts: list[Receipt] = list(receipts or [])
        self._turn = turn
        # Everything present at construction came out of the checkpoint, so it
        # is by definition earlier work. Marking the boundary here rather than
        # comparing turn numbers keeps it exact: the turn counter can stall when
        # message history is trimmed, and a stalled counter would quietly stop
        # rendering earlier turns as earlier.
        self._carried = len(self._episodes)

    @property
    def turn(self) -> int:
        return self._turn

    @property
    def episodes(self) -> list[Episode]:
        return list(self._episodes)

    @property
    def receipts(self) -> list[Receipt]:
        return list(self._receipts)

    def append_episode(self, task: str, outcome: str) -> None:
        task, outcome = task.strip(), outcome.strip()
        if not task or not outcome:
            return
        self._episodes.append(
            Episode(
                task=_within(task, _stored_entry_max()), outcome=_within(outcome, _stored_entry_max()), turn=self._turn
            )
        )
        if self._max_entries > 0:
            shed = max(0, len(self._episodes) - self._max_entries)
            del self._episodes[:shed]
            self._carried = max(0, self._carried - shed)

    def record_receipt(
        self,
        *,
        path: str,
        source: str,
        purpose: str,
        sandbox_id: str,
        rows: int | None = None,
        columns: list[str] | None = None,
    ) -> None:
        """Note a file holding data that would otherwise have to be re-fetched."""
        if not path:
            return
        # Keyed on path: the same call re-run writes a new path, and a rewrite of
        # the same path is the same data. Either way one entry per file.
        self._receipts = [receipt for receipt in self._receipts if receipt.path != path]
        self._receipts.append(
            Receipt(
                path=path,
                source=source,
                purpose=purpose.strip()[:_RECEIPT_PURPOSE_MAX],
                sandbox_id=sandbox_id,
                turn=self._turn,
                rows=rows,
                columns=list(columns or [])[:_RECEIPT_COLUMNS_MAX],
            )
        )
        if self._max_receipts > 0:
            del self._receipts[: max(0, len(self._receipts) - self._max_receipts)]

    def render_receipts(self, sandbox_id: str | None, budget: int) -> str:
        """The manifest of files still readable in ``sandbox_id``, within budget.

        Filtering on the sandbox is what keeps this honest: a receipt naming a
        file in a sandbox that no longer exists is worse than no receipt, since
        it sends a sub-agent to read something that is not there.
        """
        if budget < _MIN_ENTRY_CHARS or not sandbox_id:
            return ""
        heading = (
            "Data already fetched and saved in this sandbox — read these files instead of re-running the work "
            "that produced them:"
        )
        lines: list[str] = []
        # The heading comes out of the budget rather than sitting on top of it,
        # so the section a caller sizes is the section it gets.
        remaining = budget - len(heading) - 1
        for receipt in reversed(self._receipts):
            if receipt.sandbox_id != sandbox_id:
                continue
            entry = receipt.render()
            if len(entry) + 1 > remaining:
                break
            lines.append(entry)
            remaining -= len(entry) + 1
        if not lines:
            return ""
        lines.reverse()
        return heading + "\n" + "\n".join(lines)

    def render_episodes(self, budget: int, *, carried_only: bool = False) -> str:
        """Prior work as prose; ``carried_only`` limits it to earlier turns."""
        if budget < _MIN_ENTRY_CHARS:
            return ""
        episodes = self._episodes[: self._carried] if carried_only else self._episodes
        if not episodes:
            return ""
        heading = "Established earlier in this conversation:" if carried_only else "Established so far:"
        entries = _render_episodes(episodes, budget - len(heading) - 1)
        if not entries:
            return ""
        return heading + "\n" + "\n\n".join(entries)

    def to_state(self) -> dict[str, Any]:
        """A plain-dict form for the LangGraph checkpoint."""
        return {
            "turn": self._turn,
            "episodes": [{"task": e.task, "outcome": e.outcome, "turn": e.turn} for e in self._episodes],
            "receipts": [
                {
                    "path": r.path,
                    "source": r.source,
                    "purpose": r.purpose,
                    "sandbox_id": r.sandbox_id,
                    "turn": r.turn,
                    "rows": r.rows,
                    "columns": r.columns,
                }
                for r in self._receipts
            ],
        }

    @classmethod
    def from_state(cls, data: Any, *, turn: int | None = None) -> "SessionLedger":
        """Rebuild from what a previous turn stored, for turn ``turn``.

        Without an explicit ``turn`` this assumes it is being read one turn
        later than it was written. That is wrong for a node that runs more than
        once per turn -- the dispatcher runs once per verify/retry cycle -- so
        callers that know the real turn number should pass it.

        Tolerant of anything it does not recognize: this comes back out of a
        checkpoint that may have been written by an older build, and an
        unreadable memory must degrade to an empty one rather than fail a turn.
        """
        if not isinstance(data, dict):
            return cls(turn=max(1, turn or 1))
        episodes: list[Episode] = []
        for item in data.get("episodes") or []:
            if isinstance(item, dict) and item.get("task") and item.get("outcome"):
                episodes.append(
                    Episode(task=str(item["task"]), outcome=str(item["outcome"]), turn=int(item.get("turn") or 0))
                )
        receipts: list[Receipt] = []
        for item in data.get("receipts") or []:
            if not isinstance(item, dict) or not item.get("path"):
                continue
            rows = item.get("rows")
            columns = item.get("columns")
            receipts.append(
                Receipt(
                    path=str(item["path"]),
                    source=str(item.get("source") or "a tool"),
                    purpose=str(item.get("purpose") or ""),
                    sandbox_id=str(item.get("sandbox_id") or ""),
                    turn=int(item.get("turn") or 0),
                    rows=int(rows) if isinstance(rows, int) else None,
                    columns=[str(column) for column in columns] if isinstance(columns, list) else [],
                )
            )
        stored_turn = int(data.get("turn") or 0)
        # Never below what was stored: the label must not go backwards when a
        # trimmed history makes the caller's count smaller than it was.
        resolved = max(turn if turn is not None else stored_turn + 1, stored_turn, 1)
        return cls(episodes=episodes, receipts=receipts, turn=resolved)


class EpisodeLog:
    """Bounded append-only record of sub-agent work within a single step.

    Appends also reach the ambient :class:`SessionLedger`, so what a step learns
    outlives the step; recall reads back from both.
    """

    def __init__(self, *, max_entries: int | None = None, ledger: SessionLedger | None = None) -> None:
        self._max_entries = settings.CHAT_EPISODIC_MAX_ENTRIES if max_entries is None else max_entries
        self._episodes: list[Episode] = []
        self._ledger = ledger

    def __len__(self) -> int:
        return len(self._episodes)

    @property
    def episodes(self) -> list[Episode]:
        return list(self._episodes)

    def append(self, task: str, outcome: str) -> None:
        task, outcome = task.strip(), outcome.strip()
        if not task or not outcome:
            return
        turn = self._ledger.turn if self._ledger is not None else 0
        self._episodes.append(Episode(task=task, outcome=outcome, turn=turn))
        if self._max_entries > 0:
            # Shed the oldest: a later sub-agent's ground is likelier to be the
            # ground the next one is about to cover.
            del self._episodes[: max(0, len(self._episodes) - self._max_entries)]
        if self._ledger is not None:
            self._ledger.append_episode(task, outcome)

    def recall(self, *, max_chars: int | None = None, sandbox_id: str | None = None) -> str:
        """Render the log for a sub-agent prompt, newest first, within a budget.

        ``sandbox_id`` is the sandbox the sub-agent is about to run in, and it
        gates the receipt manifest: files are only advertised where they can
        actually be read.
        """
        budget = settings.CHAT_EPISODIC_RECALL_MAX_CHARS if max_chars is None else max_chars
        if budget <= 0:
            return ""
        # The boundary preamble and tags are context the caller pays for, so
        # they come out of the budget rather than sitting on top of it.
        budget -= fence_overhead()
        if budget < _MIN_ENTRY_CHARS:
            return ""

        sections: list[str] = []
        remaining = budget
        # Receipts first, and rendered first: they are the cheapest way to stop
        # a sub-agent re-fetching, so they get their share before prose does.
        if self._ledger is not None:
            receipts = self._ledger.render_receipts(sandbox_id, int(remaining * _RECEIPT_BUDGET_SHARE))
            if receipts:
                sections.append(receipts)
                remaining -= len(receipts) + 2

        entries = _render_episodes(self._episodes, remaining)
        if entries:
            step_text = "\n\n".join(entries)
            sections.append(step_text)
            remaining -= len(step_text) + 2

        # Earlier turns last, because this step's own findings are closer to
        # what the next sub-agent is about to do, and whatever budget is left
        # after them is what earlier turns are worth.
        if self._ledger is not None and remaining >= _MIN_ENTRY_CHARS:
            prior = self._ledger.render_episodes(remaining, carried_only=True)
            if prior:
                sections.append(prior)

        if not sections:
            return ""
        # Fenced: an outcome is a sub-agent's report of what graph and tool data
        # said, so it carries that data's content and can carry text shaped like
        # an instruction with it. Replaying it into the next sub-agent's prompt
        # is exactly where that would take effect.
        return fenced_within("\n\n".join(sections), budget)


_current_episode_log: ContextVar[EpisodeLog | None] = ContextVar("_current_episode_log", default=None)
_current_session_ledger: ContextVar[SessionLedger | None] = ContextVar("_current_session_ledger", default=None)


def start_episode_log() -> EpisodeLog:
    """Begin a fresh log for the current step and make it the ambient one."""
    log = EpisodeLog(ledger=_current_session_ledger.get())
    _current_episode_log.set(log)
    return log


def current_episode_log() -> EpisodeLog | None:
    return _current_episode_log.get()


def clear_episode_log() -> None:
    _current_episode_log.set(None)


def turn_number(messages: list[Any]) -> int:
    """Which turn of the conversation this is, counted from its user messages.

    A node's own invocation count will not do: the dispatcher runs once per
    verify/retry cycle, so deriving the turn from "one later than what was
    stored" made a second question land on turn 5. The user messages are the
    turns, by definition, and every node has them.
    """
    return max(1, sum(1 for message in messages if getattr(message, "type", "") == "human"))


def start_session_ledger(state_value: Any = None, *, turn: int | None = None) -> SessionLedger:
    """Make the conversation's ledger ambient for this turn.

    Set once per turn, before any step starts: ``asyncio.gather`` copies the
    context but not the object, so every step of the turn appends to the same
    ledger while keeping its own :class:`EpisodeLog`.
    """
    ledger = SessionLedger.from_state(state_value, turn=turn)
    _current_session_ledger.set(ledger)
    return ledger


def current_session_ledger() -> SessionLedger | None:
    return _current_session_ledger.get()


def clear_session_ledger() -> None:
    _current_session_ledger.set(None)


def session_digest(
    ledger: SessionLedger | None,
    *,
    sandbox_id: str | None,
    max_chars: int | None = None,
) -> str:
    """What earlier turns already established, for a top-level agent's prompt.

    The sub-agent prompt gets :meth:`EpisodeLog.recall`; this is the same
    material for the model that decides *whether to delegate at all*. Without
    it, the planner plans a re-fetch and the carry only saves the sub-agent from
    repeating the work its own turn already did.
    """
    budget = settings.CHAT_SESSION_MEMORY_DIGEST_MAX_CHARS if max_chars is None else max_chars
    if ledger is None or budget <= 0:
        return ""
    budget -= fence_overhead()
    if budget < _MIN_ENTRY_CHARS:
        return ""
    sections: list[str] = []
    remaining = budget
    receipts = ledger.render_receipts(sandbox_id, int(remaining * _RECEIPT_BUDGET_SHARE))
    if receipts:
        sections.append(receipts)
        remaining -= len(receipts) + 2
    prior = ledger.render_episodes(remaining, carried_only=True)
    if prior:
        sections.append(prior)
    if not sections:
        return ""
    return fenced_within("\n\n".join(sections), budget)
