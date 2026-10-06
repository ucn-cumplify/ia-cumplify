from chat_doubles import make_passages, run_chat

from ia_cumplify.adapters.outbound.fake import FAKE_CHAT_VERSION, FakeChatResponder
from ia_cumplify.application.use_cases.answer_chat import NO_PASSAGES_TEXT
from ia_cumplify.domain.chat import ChatCitation, ChatUsage


def test_cites_the_first_passage_with_zero_usage() -> None:
    passages = make_passages(3)
    run = run_chat(passages=passages, responder=FakeChatResponder())  # type: ignore[arg-type]
    assert len(run.deltas) > 1
    assert run.text.endswith("[1].")
    assert "[P" not in run.text
    done = run.done
    assert done.citations == (ChatCitation(n=1, id=passages[0].id),)
    assert (done.coverage, done.citations_dropped, done.finish_reason) == ("answered", 0, "stop")
    assert done.usage == ChatUsage(llm_calls=0)
    assert done.chat_version == FAKE_CHAT_VERSION == "fake-v1@fake"


def test_without_passages_gives_the_same_refusal_as_the_real_responder() -> None:
    run = run_chat(passages=(), responder=FakeChatResponder())  # type: ignore[arg-type]
    assert run.deltas == [NO_PASSAGES_TEXT]
    assert (run.done.coverage, run.done.usage, run.done.chat_version) == (
        "no_passages",
        ChatUsage(llm_calls=0),
        "fake-v1@fake",
    )
