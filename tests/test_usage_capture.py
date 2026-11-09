"""Cost and cache-usage capture (plan item 1: Measure; item 4: stable prefix).

WHY THIS FILE EXISTS. The harness computed tokens and cost per call, then
dropped them: the CLI printed them and nothing persisted them, so no run
could answer "what did this artifact cost, and what did its judge cost?".
The Anthropic adapter also never asked for prompt caching, so a validator
re-reading the same spec, template and upstream artifacts on every
convergence iteration paid full input price each time.

This slice:
1. Captures cache-read and cache-write tokens from the provider and prices
   them (read 0.1x input, 5-minute write 1.25x input; per-model override
   because some newer models price cache reads differently).
2. Marks the stable part of every request as a cache prefix: the system
   block (spec) and the user block holding prompt, template, upstream
   artifacts and declared inputs. The per-iteration part (current artifact,
   correction constraints) stays uncached after it. The text the model sees
   is unchanged; only the block boundaries move.
3. Records usage (model, tokens, cache tokens, cost) on every convergence
   ledger entry for the generator, the validator, and each lens, so it
   lands in .aieos/verdicts.jsonl beside the verdict it paid for.

Run: pytest tests/test_usage_capture.py
"""

import json
from types import SimpleNamespace

import pytest

from src.adapters.anthropic import AnthropicAdapter
from src.adapters.mock import MockAdapter
from src.convergence import ConvergenceLoop
from src.models import AgentRequest, AgentResponse, LifecycleEvent

SONNET_4 = "claude-sonnet-4-20250514"  # input 0.003 / output 0.015 per 1K

PASS_JSON = json.dumps(
    {
        "status": "PASS",
        "summary": "ok",
        "hard_gates": {"g1": "PASS"},
        "blocking_issues": [],
        "warnings": [],
        "completeness_score": 90,
    }
)


def _request(current_artifact=None, constraints=None):
    return AgentRequest(
        artifact_type="SAD",
        event=LifecycleEvent.PRE_VALIDATION,
        spec_content="SPEC",
        template_content="TMPL",
        prompt_content="PROMPT",
        upstream_artifacts={"PRD-1": "UPSTREAM"},
        current_artifact=current_artifact,
        correction_constraints=constraints or [],
        metadata={"artifact_id": "SAD-TEST-001"},
        declared_inputs={"principles": "INPUTS"},
    )


class _FakeMessages:
    def __init__(self, usage):
        self.usage = usage
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(
            id="msg_test",
            stop_reason="end_turn",
            content=[SimpleNamespace(text="out")],
            usage=self.usage,
        )


def _adapter_with(usage, model=SONNET_4):
    adapter = AnthropicAdapter(model=model, api_key="k")
    fake = _FakeMessages(usage)
    adapter._client = SimpleNamespace(messages=fake)
    return adapter, fake


def _usage(input_tokens=200, output_tokens=100, cache_read=0, cache_write=0):
    return SimpleNamespace(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_input_tokens=cache_read,
        cache_creation_input_tokens=cache_write,
    )


class TestAnthropicCacheUsage:
    def test_cache_tokens_are_captured(self):
        adapter, _ = _adapter_with(_usage(cache_read=3000, cache_write=1000))
        response = adapter.invoke(_request(current_artifact="ART"))
        assert response.cache_read_tokens == 3000
        assert response.cache_write_tokens == 1000
        # tokens_in keeps meaning TOTAL input; the cache fields break it down.
        assert response.tokens_in == 200 + 3000 + 1000

    def test_cache_tokens_are_priced(self):
        # input_tokens excludes cached tokens (API contract), so each bucket
        # is priced on its own: 200 fresh + 1000 written @1.25x + 3000 read
        # @0.1x + 100 out.
        adapter, _ = _adapter_with(_usage(cache_read=3000, cache_write=1000))
        response = adapter.invoke(_request(current_artifact="ART"))
        expected = (
            200 * 0.003 + 1000 * 0.003 * 1.25 + 3000 * 0.003 * 0.1 + 100 * 0.015
        ) / 1000
        assert response.cost_usd == pytest.approx(round(expected, 6))

    def test_missing_cache_fields_count_as_zero(self):
        usage = SimpleNamespace(
            input_tokens=200,
            output_tokens=100,
            cache_read_input_tokens=None,
            cache_creation_input_tokens=None,
        )
        adapter, _ = _adapter_with(usage)
        response = adapter.invoke(_request())
        assert response.cache_read_tokens == 0
        assert response.cache_write_tokens == 0
        assert response.cost_usd == pytest.approx((200 * 0.003 + 100 * 0.015) / 1000)


class TestStablePrefixIsCacheable:
    def test_system_block_is_cache_marked(self):
        adapter, fake = _adapter_with(_usage())
        adapter.invoke(_request(current_artifact="ART"))
        system = fake.kwargs["system"]
        assert system == [
            {"type": "text", "text": "SPEC", "cache_control": {"type": "ephemeral"}}
        ]

    def test_prefix_cached_and_per_iteration_suffix_not(self):
        adapter, fake = _adapter_with(_usage())
        adapter.invoke(_request(current_artifact="ART", constraints=["fix g1"]))
        blocks = fake.kwargs["messages"][0]["content"]
        assert len(blocks) == 2
        prefix, suffix = blocks
        assert prefix["cache_control"] == {"type": "ephemeral"}
        assert "UPSTREAM" in prefix["text"] and "INPUTS" in prefix["text"]
        assert "ART" not in prefix["text"]
        assert "cache_control" not in suffix
        assert "ART" in suffix["text"] and "fix g1" in suffix["text"]

    def test_model_sees_the_same_text_as_before(self):
        # Moving block boundaries must not change one character of input.
        req = _request(current_artifact="ART", constraints=["fix g1"])
        adapter, fake = _adapter_with(_usage())
        adapter.invoke(req)
        sent = "".join(b["text"] for b in fake.kwargs["messages"][0]["content"])
        _, user_message = AnthropicAdapter._build_messages(req)
        assert sent == user_message

    def test_generation_request_sends_prefix_only(self):
        adapter, fake = _adapter_with(_usage())
        adapter.invoke(_request())
        blocks = fake.kwargs["messages"][0]["content"]
        assert len(blocks) == 1
        assert blocks[0]["cache_control"] == {"type": "ephemeral"}


class _PricedMock(MockAdapter):
    """Mock that reports distinct usage so ledger entries can be told apart."""

    def __init__(self, *args, cost, cache_read, **kwargs):
        super().__init__(*args, **kwargs)
        self._cost = cost
        self._cache_read = cache_read

    def invoke(self, request):
        base = super().invoke(request)
        return AgentResponse(
            content=base.content,
            provider=base.provider,
            model=base.model,
            tokens_in=base.tokens_in,
            tokens_out=base.tokens_out,
            cost_usd=self._cost,
            latency_ms=base.latency_ms,
            cache_read_tokens=self._cache_read,
            cache_write_tokens=0,
        )


def _gen_val_requests():
    gen = _request()
    val = _request()
    return gen, val


class TestLedgerCarriesUsage:
    def test_validation_entry_records_generator_and_judge_usage(self):
        gen = _PricedMock(model_name="gen-model", cost=0.02, cache_read=0)
        val = _PricedMock(
            model_name="judge-model",
            cost=0.005,
            cache_read=900,
            preset_responses={"SAD": PASS_JSON},
        )
        _, _, state = ConvergenceLoop(gen, val).run(*_gen_val_requests())
        usage = state.ledger[0]["usage"]
        assert usage["generate"] == {
            "model": "gen-model",
            "tokens_in": 100,
            "tokens_out": 200,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
            "cost_usd": 0.02,
        }
        assert usage["validate"]["model"] == "judge-model"
        assert usage["validate"]["cache_read_tokens"] == 900
        assert usage["validate"]["cost_usd"] == 0.005

    def test_lens_entry_records_lens_usage(self):
        gen = _PricedMock(cost=0.02, cache_read=0)
        val = _PricedMock(cost=0.005, cache_read=0, preset_responses={"SAD": PASS_JSON})
        lens = _PricedMock(
            model_name="lens-model",
            cost=0.001,
            cache_read=50,
            preset_responses={"SAD": PASS_JSON},
        )
        loop = ConvergenceLoop(gen, val, lens_adapters={"security": lens})
        _, _, state = loop.run(*_gen_val_requests())
        lens_entry = next(e for e in state.ledger if e.get("lens") == "security")
        assert lens_entry["usage"]["lens"]["model"] == "lens-model"
        assert lens_entry["usage"]["lens"]["cost_usd"] == 0.001
        assert lens_entry["usage"]["lens"]["cache_read_tokens"] == 50
