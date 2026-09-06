import re
from uuid import UUID

import pytest
from pydantic import ValidationError

from reporting.schema.chat import ChatTurnItem
from reporting.schema.ids import STORE_ID_MAX_LENGTH, STORE_ID_PATTERN
from reporting.services import report_store


def _turn(thread_id: str) -> ChatTurnItem:
    return ChatTurnItem(
        turn_id=report_store.generate_id(),
        thread_id=thread_id,
        user_id="user-1",
        message_id="message-1",
        text_id="text-1",
        idempotency_key="request-1",
        command={"message": "hello", "permission_cap": [], "timeout_seconds": 60},
        created_at="2024-01-01T00:00:00+00:00",
        updated_at="2024-01-01T00:00:00+00:00",
        expires_at="2024-01-01T00:01:00+00:00",
    )


def test_current_generator_satisfies_the_shared_store_id_contract() -> None:
    generated = report_store.generate_id()

    assert UUID(generated).version == 7
    assert len(generated) <= STORE_ID_MAX_LENGTH
    assert re.fullmatch(STORE_ID_PATTERN, generated)
    assert _turn(generated).thread_id == generated


@pytest.mark.parametrize("legacy_id", ["1", "12345678901234567890"])
def test_legacy_decimal_ids_remain_addressable(legacy_id: str) -> None:
    assert re.fullmatch(STORE_ID_PATTERN, legacy_id)
    assert _turn(legacy_id).thread_id == legacy_id


@pytest.mark.parametrize(
    "invalid_id",
    ["", "not-an-id", "123456789012345678901", "01991f9d-39f0-7eca-a454-98bbacf9910z"],
)
def test_store_id_contract_rejects_other_shapes(invalid_id: str) -> None:
    assert re.fullmatch(STORE_ID_PATTERN, invalid_id) is None
    with pytest.raises(ValidationError):
        _turn(invalid_id)
