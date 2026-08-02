"""What sub-agents already did, so the next one does not start from nothing.

``sandbox__delegate`` opens a fresh backend and a fresh ``create_react_agent``
per call, so delegation N+1 knows nothing about N. One observed chat step made
136 delegations and, inside them, 678 ``graph__query`` and 73 ``graph__schema``
calls to answer a question about eight named CVEs -- it re-derived the same
ground over and over because nothing carried across the boundary. That step
spent 79,928 of the run's 96,000 spendable tokens and starved every step after
it.

This is the missing carry: an append-only, bounded record of each sub-agent's
task and outcome, replayed into the next sub-agent's prompt.

**Scope is by construction, not by key.** A log is created for one step, held in
a :class:`~contextvars.ContextVar`, and discarded when the step ends. There is
no registry and no key, so there is no key to get wrong -- one user's work
cannot be addressed from another user's request because it is never reachable
outside the context that created it. Parallel steps get independent logs
(``asyncio.gather`` copies the context per task); delegations inside one step
share the object by reference, which is exactly the carry we want.

Deliberately in-process and turn-scoped. Recall across *turns* is a different
problem with different requirements (durability, RBAC re-checks at read,
retention) and belongs in a store, not here.
"""

from contextvars import ContextVar
from dataclasses import dataclass

from reporting import settings

# Below this an entry is a stub rather than a recollection, so drop it instead.
_MIN_ENTRY_CHARS = 120


@dataclass(frozen=True)
class Episode:
    """One sub-agent's assignment and what it reported back."""

    task: str
    outcome: str


class EpisodeLog:
    """Bounded append-only record of sub-agent work within a single step."""

    def __init__(self, *, max_entries: int | None = None) -> None:
        self._max_entries = settings.CHAT_EPISODIC_MAX_ENTRIES if max_entries is None else max_entries
        self._episodes: list[Episode] = []

    def __len__(self) -> int:
        return len(self._episodes)

    @property
    def episodes(self) -> list[Episode]:
        return list(self._episodes)

    def append(self, task: str, outcome: str) -> None:
        task, outcome = task.strip(), outcome.strip()
        if not task or not outcome:
            return
        self._episodes.append(Episode(task=task, outcome=outcome))
        if self._max_entries > 0:
            # Shed the oldest: a later sub-agent's ground is likelier to be the
            # ground the next one is about to cover.
            del self._episodes[: max(0, len(self._episodes) - self._max_entries)]

    def recall(self, *, max_chars: int | None = None) -> str:
        """Render the log for a sub-agent prompt, newest first, within a budget."""
        budget = settings.CHAT_EPISODIC_RECALL_MAX_CHARS if max_chars is None else max_chars
        if budget <= 0 or not self._episodes:
            return ""
        kept: list[str] = []
        remaining = budget
        for index, episode in enumerate(reversed(self._episodes), start=1):
            if remaining < _MIN_ENTRY_CHARS:
                break
            entry = f"{index}. Task: {episode.task}\n   Result: {episode.outcome}"
            if len(entry) > remaining:
                entry = entry[:remaining].rstrip() + "…"
            kept.append(entry)
            remaining -= len(entry) + 2
        kept.reverse()
        return "\n\n".join(kept)


_current_episode_log: ContextVar[EpisodeLog | None] = ContextVar("_current_episode_log", default=None)


def start_episode_log() -> EpisodeLog:
    """Begin a fresh log for the current step and make it the ambient one."""
    log = EpisodeLog()
    _current_episode_log.set(log)
    return log


def current_episode_log() -> EpisodeLog | None:
    return _current_episode_log.get()


def clear_episode_log() -> None:
    _current_episode_log.set(None)
