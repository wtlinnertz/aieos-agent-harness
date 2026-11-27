"""Step B: withhold the template from SAD judge calls (FR-014 build spec 4h).

The 2026-11-08 spec-over-template ruling made sad-template.md a source of
rule misattribution, and sad-validator.md never listed it as a Required
Input. Generation keeps it. The rule sits in one core function that every
adapter's request builder calls, so production validation and calibration
lose it together.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.adapters.anthropic import AnthropicAdapter
from src.adapters.openai import OpenAIAdapter
from src.calibration import GoldCase, run_calibration
from src.judge_inputs import template_for
from src.models import AgentRequest, AgentResponse, LifecycleEvent

TEMPLATE = "TEMPLATE-BODY-7f3a"


def _request(
    event: LifecycleEvent, artifact_type: str = "SAD", template: str = TEMPLATE
) -> AgentRequest:
    return AgentRequest(
        artifact_type=artifact_type,
        event=event,
        spec_content="SPEC",
        template_content=template,
        prompt_content="PROMPT",
        upstream_artifacts={"PRD-1": "UPSTREAM"},
        current_artifact="ART",
        correction_constraints=[],
        metadata={},
    )


class TestTemplateFor:
    @pytest.mark.parametrize(
        "event", [LifecycleEvent.PRE_VALIDATION, LifecycleEvent.POST_VALIDATION]
    )
    def test_template_for_sad_judge_event_returns_empty(self, event):
        assert template_for(_request(event)) == ""

    def test_template_for_sad_generation_returns_template(self):
        assert template_for(_request(LifecycleEvent.PRE_GENERATION)) == TEMPLATE

    def test_template_for_other_type_validation_returns_template(self):
        request = _request(LifecycleEvent.PRE_VALIDATION, artifact_type="PRD")
        assert template_for(request) == TEMPLATE

    def test_template_for_lowercase_sad_validation_returns_empty(self):
        request = _request(LifecycleEvent.PRE_VALIDATION, artifact_type="sad")
        assert template_for(request) == ""


BUILDERS = [AnthropicAdapter._build_messages, OpenAIAdapter._build_messages]


class TestBuilders:
    @pytest.mark.parametrize("build", BUILDERS)
    def test_builder_sad_validation_omits_template_section(self, build):
        _, user = build(_request(LifecycleEvent.PRE_VALIDATION))
        assert TEMPLATE not in user
        assert "## Template" not in user
        assert "UPSTREAM" in user and "ART" in user

    @pytest.mark.parametrize("build", BUILDERS)
    def test_builder_sad_generation_keeps_template_section(self, build):
        _, user = build(_request(LifecycleEvent.PRE_GENERATION))
        assert "## Template\n\n" + TEMPLATE in user

    @pytest.mark.parametrize("build", BUILDERS)
    def test_builder_other_type_validation_keeps_template_section(self, build):
        _, user = build(_request(LifecycleEvent.PRE_VALIDATION, artifact_type="PRD"))
        assert TEMPLATE in user


class _FakeMessages:
    def __init__(self) -> None:
        self.kwargs: dict = {}

    def create(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(
            id="msg_test",
            content=[SimpleNamespace(text="ok")],
            usage=SimpleNamespace(
                input_tokens=1,
                output_tokens=1,
                cache_read_input_tokens=0,
                cache_creation_input_tokens=0,
            ),
            stop_reason="end_turn",
        )


def _anthropic() -> tuple[AnthropicAdapter, _FakeMessages]:
    adapter = AnthropicAdapter(model="claude-haiku-4-5-20251001", api_key="k")
    fake = _FakeMessages()
    adapter._client = SimpleNamespace(messages=fake)
    return adapter, fake


class TestAnthropicInvoke:
    def test_invoke_sad_validation_sends_no_template(self):
        adapter, fake = _anthropic()
        adapter.invoke(_request(LifecycleEvent.PRE_VALIDATION))
        sent = "".join(b["text"] for b in fake.kwargs["messages"][0]["content"])
        assert TEMPLATE not in sent

    def test_invoke_sad_validation_hash_ignores_withheld_template(self):
        adapter, _ = _anthropic()
        one = adapter.invoke(_request(LifecycleEvent.PRE_VALIDATION, template="A"))
        two = adapter.invoke(_request(LifecycleEvent.PRE_VALIDATION, template="B"))
        assert one.input_content_hash == two.input_content_hash

    def test_invoke_sad_generation_hash_tracks_template(self):
        adapter, _ = _anthropic()
        one = adapter.invoke(_request(LifecycleEvent.PRE_GENERATION, template="A"))
        two = adapter.invoke(_request(LifecycleEvent.PRE_GENERATION, template="B"))
        assert one.input_content_hash != two.input_content_hash


class _Capture:
    provider_name = "capture"
    model_name = "capture-v1"

    def __init__(self) -> None:
        self.users: list[str] = []

    def invoke(self, request: AgentRequest) -> AgentResponse:
        _, user = AnthropicAdapter._build_messages(request)
        self.users.append(user)
        return AgentResponse(
            content='{"status": "PASS", "summary": "s", "hard_gates": {}, '
            '"blocking_issues": [], "warnings": [], "completeness_score": 90}',
            provider="capture",
            model="capture-v1",
            tokens_in=1,
            tokens_out=1,
            cost_usd=0.0,
            latency_ms=1.0,
        )


def test_calibration_sad_judge_message_carries_no_template(tmp_path):
    case = GoldCase(
        case_id="c1",
        artifact_type="SAD",
        validator="sad-validator",
        input_path=tmp_path / "c1.md",
        input_sha256="0" * 64,
        expected_gates={"scope_discipline": "PASS"},
        spec_exemption_case=False,
        labeled_by="synthetic",
        labeled_date="2026-11-26",
        source="synthetic test fixture",
        input_content="# SAD\n",
    )
    judge = _Capture()

    run_calibration([case], judge, validator_prompt="p", template_content=TEMPLATE)

    assert len(judge.users) == 3
    assert all(TEMPLATE not in user for user in judge.users)
