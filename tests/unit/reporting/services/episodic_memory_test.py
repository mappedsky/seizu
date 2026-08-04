import asyncio

from reporting.services import episodic_memory
from reporting.services.episodic_memory import EpisodeLog


def test_recall_replays_prior_sub_agent_results():
    log = EpisodeLog()
    log.append("count CVEs", "There are 412 CVE nodes.")
    log.append("find the schema", "Labels: CVE, Repository, Dependency.")

    recall = log.recall(max_chars=4000)

    assert "412 CVE nodes" in recall
    assert "Labels: CVE, Repository, Dependency." in recall


def test_recall_is_empty_without_episodes():
    assert EpisodeLog().recall(max_chars=4000) == ""


def test_recall_budget_of_zero_disables_it():
    log = EpisodeLog()
    log.append("t", "o")
    assert log.recall(max_chars=0) == ""


def test_append_ignores_empty_task_or_outcome():
    log = EpisodeLog()
    log.append("", "outcome")
    log.append("task", "   ")
    assert len(log) == 0


def test_log_sheds_oldest_beyond_the_entry_cap():
    log = EpisodeLog(max_entries=3)
    for index in range(6):
        log.append(f"task {index}", f"outcome {index}")

    tasks = [episode.task for episode in log.episodes]
    # Newest kept: a later sub-agent's ground is likelier to be what the next
    # one is about to cover.
    assert tasks == ["task 3", "task 4", "task 5"]


def test_recall_keeps_newest_entries_within_the_budget():
    log = EpisodeLog()
    for index in range(10):
        log.append(f"task {index}", f"outcome {index} " + "x" * 400)

    recall = log.recall(max_chars=900)

    assert len(recall) <= 900
    assert "outcome 9" in recall
    assert "outcome 0" not in recall


async def test_parallel_steps_do_not_share_a_log():
    """Scope is by construction: gather copies the context per task."""
    seen: dict[str, int] = {}

    async def _step(name: str) -> None:
        episodic_memory.start_episode_log()
        await asyncio.sleep(0)
        log = episodic_memory.current_episode_log()
        assert log is not None
        log.append(f"{name} task", f"{name} outcome")
        await asyncio.sleep(0)
        seen[name] = len(log)

    await asyncio.gather(_step("a"), _step("b"), _step("c"))

    # Each step recorded only its own work; none saw a sibling's.
    assert seen == {"a": 1, "b": 1, "c": 1}


async def test_work_inside_one_step_shares_the_log():
    episodic_memory.start_episode_log()

    async def _delegation(index: int) -> None:
        log = episodic_memory.current_episode_log()
        assert log is not None
        log.append(f"task {index}", f"outcome {index}")

    await asyncio.gather(*(_delegation(index) for index in range(3)))

    log = episodic_memory.current_episode_log()
    assert log is not None and len(log) == 3


def test_no_ambient_log_outside_a_step():
    episodic_memory.clear_episode_log()
    assert episodic_memory.current_episode_log() is None


async def test_one_users_work_is_unreachable_from_another_request():
    """The security property: scope is by construction, so there is no key to get wrong.

    Each request runs in its own asyncio task, and a task gets a copy of the
    context, so a log started while serving one user is not visible while
    serving another. Nothing registers logs globally, so there is also no
    lookup by which a caller could reach one.
    """
    leaked: list[str] = []

    async def _request(user: str) -> None:
        episodic_memory.start_episode_log()
        log = episodic_memory.current_episode_log()
        assert log is not None
        log.append(f"{user} task", f"{user} secret finding")
        await asyncio.sleep(0)
        recall = log.recall(max_chars=4000)
        leaked.extend(other for other in ("alice", "bob") if other != user and other in recall)

    await asyncio.gather(_request("alice"), _request("bob"))

    assert leaked == []
    # And nothing survives into a request that never started a log.
    episodic_memory.clear_episode_log()
    assert episodic_memory.current_episode_log() is None
