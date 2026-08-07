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


# ---------------------------------------------------------------------------
# SessionLedger — the carry between turns
# ---------------------------------------------------------------------------


def _ledger_with_a_receipt(**kwargs) -> episodic_memory.SessionLedger:
    ledger = episodic_memory.SessionLedger(**kwargs)
    ledger.record_receipt(
        path="/home/user/seizu_results/graph__query_001.json",
        source="graph__query",
        purpose="list every critical CVE with its repository",
        sandbox_id="sbx-1",
        rows=412,
        columns=["cve_id", "severity", "repo"],
    )
    return ledger


def test_a_turns_findings_reach_the_next_turn():
    """The hole this fills: a follow-up turn re-ran the previous turn's queries
    on top of its own work, because nothing said they had been run."""
    first = episodic_memory.SessionLedger()
    first.append_episode("count CVEs", "There are 412 CVE nodes.")

    second = episodic_memory.SessionLedger.from_state(first.to_state())

    assert second.turn == 2
    assert "412 CVE nodes" in second.render_episodes(4000, carried_only=True)


def test_a_receipt_says_where_the_data_already_is():
    ledger = _ledger_with_a_receipt()
    rendered = ledger.render_receipts("sbx-1", 4000)

    assert "/home/user/seizu_results/graph__query_001.json" in rendered
    assert "412 rows" in rendered
    assert "cve_id" in rendered
    assert "list every critical CVE" in rendered


def test_a_receipt_is_not_offered_for_a_different_sandbox():
    """A resume that failed means a new sandbox and no files. Advertising them
    anyway is worse than saying nothing: it sends a sub-agent to read a file
    that is not there."""
    ledger = _ledger_with_a_receipt()
    assert ledger.render_receipts("sbx-2", 4000) == ""
    assert ledger.render_receipts("", 4000) == ""


def test_re_saving_a_path_replaces_its_receipt():
    ledger = _ledger_with_a_receipt()
    ledger.record_receipt(
        path="/home/user/seizu_results/graph__query_001.json",
        source="graph__query",
        purpose="the same file, written again",
        sandbox_id="sbx-1",
        rows=9,
    )
    assert len(ledger.receipts) == 1
    assert ledger.receipts[0].rows == 9


def test_receipts_shed_the_oldest_beyond_the_cap():
    ledger = episodic_memory.SessionLedger(max_receipts=2)
    for index in range(5):
        ledger.record_receipt(path=f"/tmp/{index}.json", source="t", purpose="", sandbox_id="sbx-1")
    assert [receipt.path for receipt in ledger.receipts] == ["/tmp/3.json", "/tmp/4.json"]


def test_a_ledger_survives_a_round_trip_through_the_checkpoint():
    ledger = _ledger_with_a_receipt()
    ledger.append_episode("count CVEs", "There are 412 CVE nodes.")

    restored = episodic_memory.SessionLedger.from_state(ledger.to_state())

    assert [(e.task, e.outcome) for e in restored.episodes] == [("count CVEs", "There are 412 CVE nodes.")]
    assert restored.receipts[0].columns == ["cve_id", "severity", "repo"]
    assert restored.receipts[0].rows == 412


def test_unreadable_stored_memory_degrades_to_an_empty_one():
    """It comes out of a checkpoint an older build may have written; a turn must
    not fail because its memory is in a shape this build does not know."""
    for value in (None, "nonsense", {"episodes": "not a list"}, {"receipts": [{"no": "path"}]}):
        ledger = episodic_memory.SessionLedger.from_state(value)
        assert ledger.episodes == []
        assert ledger.receipts == []
        assert ledger.turn == 1


def test_recall_carries_both_this_step_and_earlier_turns():
    ledger = _ledger_with_a_receipt(turn=1)
    ledger.append_episode("earlier turn work", "The prior turn established X.")
    # Round-trip so the entry arrives as carried-in work rather than this turn's.
    ledger = episodic_memory.SessionLedger.from_state(ledger.to_state())

    log = EpisodeLog(ledger=ledger)
    log.append("this step's work", "This step established Y.")

    recall = log.recall(max_chars=6000, sandbox_id="sbx-1")

    assert "This step established Y." in recall
    assert "The prior turn established X." in recall
    assert "graph__query_001.json" in recall
    # Fenced, because every part of it reports what external data said.
    assert "Security boundary" in recall


def test_an_episode_appended_in_a_step_reaches_the_session_ledger():
    ledger = episodic_memory.SessionLedger()
    log = EpisodeLog(ledger=ledger)
    log.append("task", "outcome")

    assert [(e.task, e.outcome) for e in ledger.episodes] == [("task", "outcome")]


async def test_steps_of_one_turn_share_the_ledger_but_not_their_logs():
    """Step isolation is unchanged; what they share is what the turn learned."""
    ledger = episodic_memory.start_session_ledger(None)

    async def _step(name: str) -> int:
        log = episodic_memory.start_episode_log()
        await asyncio.sleep(0)
        log.append(f"{name} task", f"{name} outcome")
        await asyncio.sleep(0)
        return len(log)

    sizes = await asyncio.gather(_step("a"), _step("b"), _step("c"))

    assert sizes == [1, 1, 1]
    assert len(ledger.episodes) == 3
    episodic_memory.clear_session_ledger()


def test_the_digest_tells_a_planner_the_data_already_exists():
    ledger = _ledger_with_a_receipt(turn=1)
    ledger.append_episode("earlier", "Established a baseline.")
    ledger = episodic_memory.SessionLedger.from_state(ledger.to_state())

    digest = episodic_memory.session_digest(ledger, sandbox_id="sbx-1", max_chars=4000)

    assert "graph__query_001.json" in digest
    assert "Established a baseline." in digest
    assert len(digest) <= 4000


def test_the_digest_is_empty_without_memory_or_budget():
    assert episodic_memory.session_digest(None, sandbox_id="sbx-1") == ""
    assert episodic_memory.session_digest(episodic_memory.SessionLedger(), sandbox_id="sbx-1") == ""
    assert episodic_memory.session_digest(_ledger_with_a_receipt(), sandbox_id="sbx-1", max_chars=0) == ""


def test_a_stored_episode_is_capped_to_what_could_ever_be_rendered(mocker):
    """The ledger rides in every checkpoint. A sub-agent result is capped at
    SANDBOX_MAX_OUTPUT_BYTES (50KB), and dozens of those would put megabytes of
    text into thread state that rendering was always going to truncate."""
    mocker.patch("reporting.settings.CHAT_EPISODIC_RECALL_MAX_CHARS", 400)
    mocker.patch("reporting.settings.CHAT_SESSION_MEMORY_DIGEST_MAX_CHARS", 200)

    ledger = episodic_memory.SessionLedger()
    ledger.append_episode("t", "x" * 50_000)

    stored = ledger.episodes[0].outcome
    assert len(stored) == 400
    assert stored.endswith("…")


def test_the_turn_number_counts_user_messages_not_node_invocations():
    """The dispatcher runs once per verify/retry cycle. Deriving the turn from
    "one later than what was stored" made a second question land on turn 5."""
    from langchain_core.messages import AIMessage, HumanMessage

    messages = [HumanMessage(content="first"), AIMessage(content="answer"), HumanMessage(content="second")]
    assert episodic_memory.turn_number(messages) == 2
    assert episodic_memory.turn_number([]) == 1

    stored = episodic_memory.SessionLedger(turn=1)
    stored.append_episode("earlier", "Established X.")
    state = stored.to_state()

    # Two reads of the same state during one turn stay on that turn.
    assert episodic_memory.SessionLedger.from_state(state, turn=2).turn == 2
    assert episodic_memory.SessionLedger.from_state(state, turn=2).turn == 2


def test_earlier_turns_stay_earlier_even_when_the_turn_counter_stalls():
    """History trimming can make a message-derived turn number stop rising. The
    boundary is what was carried in from the checkpoint, not a comparison of
    turn numbers, so a stalled counter cannot silently hide earlier work."""
    first = episodic_memory.SessionLedger(turn=4)
    first.append_episode("earlier", "Established X.")

    # The caller's count came back *lower* than what was stored.
    second = episodic_memory.SessionLedger.from_state(first.to_state(), turn=2)
    second.append_episode("now", "Established Y.")

    prior = second.render_episodes(4000, carried_only=True)
    assert "Established X." in prior
    assert "Established Y." not in prior
    assert second.turn == 4  # the label never goes backwards
