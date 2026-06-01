from langchain_core.messages import AIMessage, HumanMessage

from reporting.services.chat_messages import MessageTag, drop_tagged, has_tag, tag_message


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
