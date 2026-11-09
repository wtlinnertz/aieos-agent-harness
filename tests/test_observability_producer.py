"""DEF-001 / DEF-002 — the observability layer gets a producer.

WHY THIS FILE EXISTS. ObservabilityLayer.record() was called only from
tests. No production path wrote harness-metrics.jsonl, so `harness costs`
and `harness health` read a file nothing wrote, and calibration runs
(DEF-002) measured no cost or latency. Cost governance was silently inert.

The fix wraps every adapter that _build_adapters hands out, so every
command that invokes a provider -- generate, validate, lifecycle, research,
calibrate, run-artifact -- records one InvocationRecord per call, success
or failure. Attribute access passes through, so callers see the adapter
they always saw.

Run: pytest tests/test_observability_producer.py
"""

import pytest

from src.adapters.base import AgentAdapter
from src.adapters.mock import MockAdapter
from src.cli import _build_adapters
from src.config import HarnessConfig, ProviderConfig
from src.models import AgentRequest, LifecycleEvent, RoutingStrategy
from src.observability import ObservabilityLayer, ObservedAdapter


def _request():
    return AgentRequest(
        artifact_type="SAD",
        event=LifecycleEvent.PRE_VALIDATION,
        spec_content="spec",
        template_content="tmpl",
        prompt_content="prompt",
        upstream_artifacts={},
        current_artifact=None,
        correction_constraints=[],
        metadata={"artifact_id": "SAD-TEST-001"},
    )


class TestObservedAdapterRecords:
    def test_successful_call_is_recorded(self, tmp_path):
        layer = ObservabilityLayer(tmp_path / "metrics.jsonl")
        adapter = ObservedAdapter(
            MockAdapter(provider_name="prov", model_name="mod"), layer
        )
        response = adapter.invoke(_request())
        [record] = layer.read_records()
        assert record.artifact_type == "SAD"
        assert record.artifact_id == "SAD-TEST-001"
        assert record.event is LifecycleEvent.PRE_VALIDATION
        assert record.provider == "prov"
        assert record.model == "mod"
        assert record.strategy is RoutingStrategy.DIRECT
        assert (record.tokens_in, record.tokens_out) == (100, 200)
        assert record.cost_usd == response.cost_usd
        assert record.result == "success"
        assert record.error is None

    def test_failed_call_is_recorded_and_reraised(self, tmp_path):
        layer = ObservabilityLayer(tmp_path / "metrics.jsonl")
        adapter = ObservedAdapter(
            MockAdapter(should_fail=True, failure_message="boom"), layer
        )
        with pytest.raises(RuntimeError, match="boom"):
            adapter.invoke(_request())
        [record] = layer.read_records()
        assert record.result == "error"
        assert "boom" in record.error
        assert record.cost_usd == 0.0

    def test_still_an_agent_adapter(self, tmp_path):
        layer = ObservabilityLayer(tmp_path / "metrics.jsonl")
        adapter = ObservedAdapter(MockAdapter(), layer)
        assert isinstance(adapter, AgentAdapter)


class TestCliAdaptersAreObserved:
    def _config(self, tmp_path):
        cfg = HarnessConfig()
        cfg.observability_log = str(tmp_path / "harness-metrics.jsonl")
        cfg.providers = {"mock": ProviderConfig(enabled=True)}
        return cfg

    def test_cli_adapter_invocations_reach_the_log(self, tmp_path):
        cfg = self._config(tmp_path)
        adapters = _build_adapters(cfg)
        adapters["mock"].invoke(_request())
        layer = ObservabilityLayer(tmp_path / "harness-metrics.jsonl")
        assert len(layer.read_records()) == 1

    def test_harness_costs_reads_what_was_written(self, tmp_path):
        # The reader `harness costs` uses now has data to read.
        cfg = self._config(tmp_path)
        adapters = _build_adapters(cfg)
        adapters["mock"].invoke(_request())
        adapters["mock"].invoke(_request())
        summary = ObservabilityLayer(tmp_path / "harness-metrics.jsonl").cost_summary()
        assert "SAD" in summary["cost_by_artifact_type"]
