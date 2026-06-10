from langchain_core.messages import AIMessage, HumanMessage

from reporting.services.chat_messages import MessageTag, drop_tagged, has_tag, message_text, tag_message


def test_message_text_plain_string():
    assert message_text("hello") == "hello"


def test_message_text_list_of_strings():
    assert message_text(["foo", "bar"]) == "foobar"


def test_message_text_list_of_text_dicts():
    assert message_text([{"type": "text", "text": "hi"}, {"type": "text", "text": " there"}]) == "hi there"


def test_message_text_list_mixed_drops_non_text_blocks():
    assert message_text([{"type": "thinking", "thinking": "..."}, {"type": "text", "text": "answer"}]) == "answer"


def test_message_text_empty_list():
    assert message_text([]) == ""


def test_message_text_unexpected_type_returns_empty():
    assert message_text(42) == ""


def test_tag_message_is_idempotent_and_detectable():
    message = HumanMessage(content="hi")
    assert not has_tag(message, MessageTag.EPHEMERAL)

    tag_message(message, MessageTag.EPHEMERAL)
    tag_message(message, MessageTag.EPHEMERAL)

    assert has_tag(message, MessageTag.EPHEMERAL)
    assert message.additional_kwargs["seizu_tags"] == [MessageTag.EPHEMERAL.value]


def test_has_tag_tolerates_non_messages():
    assert not has_tag(object(), MessageTag.EPHEMERAL)
    assert not has_tag({"additional_kwargs": "nope"}, MessageTag.EPHEMERAL)


def test_drop_tagged_filters_only_tagged():
    keep_user = HumanMessage(content="keep")
    keep_ai = AIMessage(content="keep too")
    drop = tag_message(HumanMessage(content="drop"), MessageTag.EPHEMERAL)

    result = drop_tagged([keep_user, drop, keep_ai], MessageTag.EPHEMERAL)

    assert result == [keep_user, keep_ai]
