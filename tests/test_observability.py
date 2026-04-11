"""Tests for the observability layer."""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.models import AgentSpecies, InvocationRecord, LifecycleEvent, RoutingStrategy
from src.observability import ObservabilityLayer


def _make_record(
    artifact_type: str = "SAD",
    artifact_id: str = "SAD-TEST-001",
    provider: str = "anthropic",
    model: str = "claude-sonnet-4-20250514",
    cost_usd: float = 0.05,
    latency_ms: float = 1200.0,
    result: str = "success",
    timestamp: str | None = None,
    error: str | None = None,
) -> InvocationRecord:
    return InvocationRecord(
        timestamp=timestamp or datetime.now(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        ),
        artifact_type=artifact_type,
        artifact_id=artifact_id,
        event=LifecycleEvent.POST_GENERATION,
        provider=provider,
        model=model,
        strategy=RoutingStrategy.FALLBACK,
        tokens_in=500,
        tokens_out=1000,
        cost_usd=cost_usd,
        latency_ms=latency_ms,
        result=result,
        validation_status=None,
        convergence_iteration=1,
        error=error,
    )


class TestRecord:
    """record() appends JSONL lines."""

    def test_appends_jsonl_line(self, tmp_path: Path):
        log = tmp_path / "metrics.jsonl"
        obs = ObservabilityLayer(log)
        record = _make_record()

        obs.record(record)

        lines = log.read_text().strip().split("\n")
        assert len(lines) == 1
        data = json.loads(lines[0])
        assert data["artifact_type"] == "SAD"
        assert data["provider"] == "anthropic"

    def test_appends_multiple_lines(self, tmp_path: Path):
        log = tmp_path / "metrics.jsonl"
        obs = ObservabilityLayer(log)

        obs.record(_make_record(artifact_type="SAD"))
        obs.record(_make_record(artifact_type="TDD"))
        obs.record(_make_record(artifact_type="PRD"))

        lines = log.read_text().strip().split("\n")
        assert len(lines) == 3

    def test_creates_parent_directories(self, tmp_path: Path):
        log = tmp_path / "subdir" / "deep" / "metrics.jsonl"
        obs = ObservabilityLayer(log)
        obs.record(_make_record())
        assert log.exists()


class TestReadRecords:
    """read_records() round-trips correctly."""

    def test_round_trip(self, tmp_path: Path):
        log = tmp_path / "metrics.jsonl"
        obs = ObservabilityLayer(log)
        original = _make_record(
            artifact_type="TDD",
            cost_usd=0.123,
            latency_ms=999.5,
        )
        obs.record(original)

        records = obs.read_records()
        assert len(records) == 1
        r = records[0]
        assert r.artifact_type == "TDD"
        assert r.cost_usd == 0.123
        assert r.latency_ms == 999.5
        assert r.event == LifecycleEvent.POST_GENERATION
        assert r.strategy == RoutingStrategy.FALLBACK

    def test_filter_by_timestamp(self, tmp_path: Path):
        log = tmp_path / "metrics.jsonl"
        obs = ObservabilityLayer(log)

        old_ts = "2026-03-20T10:00:00Z"
        new_ts = "2026-03-25T10:00:00Z"

        obs.record(_make_record(artifact_type="OLD", timestamp=old_ts))
        obs.record(_make_record(artifact_type="NEW", timestamp=new_ts))

        since = datetime(2026, 3, 24, tzinfo=timezone.utc)
        records = obs.read_records(since=since)
        assert len(records) == 1
        assert records[0].artifact_type == "NEW"

    def test_empty_file(self, tmp_path: Path):
        log = tmp_path / "metrics.jsonl"
        obs = ObservabilityLayer(log)
        records = obs.read_records()
        assert records == []


class TestCostSummary:
    """cost_summary() aggregates by provider and artifact type."""

    def test_aggregate(self, tmp_path: Path):
        log = tmp_path / "metrics.jsonl"
        obs = ObservabilityLayer(log)

        obs.record(_make_record(provider="anthropic", artifact_type="SAD", cost_usd=0.10))
        obs.record(_make_record(provider="anthropic", artifact_type="TDD", cost_usd=0.05))
        obs.record(_make_record(provider="openai", artifact_type="SAD", cost_usd=0.08))

        summary = obs.cost_summary()
        assert summary["invocation_count"] == 3
        assert summary["total_cost"] == pytest.approx(0.23, abs=1e-6)
        assert summary["cost_by_provider"]["anthropic"] == pytest.approx(0.15, abs=1e-6)
        assert summary["cost_by_provider"]["openai"] == pytest.approx(0.08, abs=1e-6)
        assert summary["cost_by_artifact_type"]["SAD"] == pytest.approx(0.18, abs=1e-6)
        assert summary["cost_by_artifact_type"]["TDD"] == pytest.approx(0.05, abs=1e-6)

    def test_filter_by_initiative(self, tmp_path: Path):
        log = tmp_path / "metrics.jsonl"
        obs = ObservabilityLayer(log)

        obs.record(_make_record(artifact_id="SAD-ALPHA-001", cost_usd=0.10))
        obs.record(_make_record(artifact_id="SAD-BETA-001", cost_usd=0.20))

        summary = obs.cost_summary(initiative="ALPHA")
        assert summary["invocation_count"] == 1
        assert summary["total_cost"] == pytest.approx(0.10, abs=1e-6)

    def test_empty_log(self, tmp_path: Path):
        log = tmp_path / "metrics.jsonl"
        obs = ObservabilityLayer(log)
        summary = obs.cost_summary()
        assert summary["invocation_count"] == 0
        assert summary["total_cost"] == 0.0


class TestProviderHealthSummary:
    """provider_health_summary() calculates failure rates."""

    def test_healthy_provider(self, tmp_path: Path):
        log = tmp_path / "metrics.jsonl"
        obs = ObservabilityLayer(log)

        obs.record(_make_record(provider="anthropic", result="success"))
        obs.record(_make_record(provider="anthropic", result="success"))

        health = obs.provider_health_summary()
        assert health["anthropic"]["total_invocations"] == 2
        assert health["anthropic"]["failures"] == 0
        assert health["anthropic"]["current_status"] == "OK"

    def test_degraded_provider(self, tmp_path: Path):
        log = tmp_path / "metrics.jsonl"
        obs = ObservabilityLayer(log)

        obs.record(_make_record(provider="openai", result="success"))
        obs.record(_make_record(provider="openai", result="success"))
        obs.record(_make_record(provider="openai", result="failure"))

        health = obs.provider_health_summary()
        assert health["openai"]["failures"] == 1
        assert health["openai"]["current_status"] == "DEGRADED"

    def test_down_provider(self, tmp_path: Path):
        log = tmp_path / "metrics.jsonl"
        obs = ObservabilityLayer(log)

        obs.record(_make_record(provider="openai", result="failure"))
        obs.record(_make_record(provider="openai", result="failure"))

        health = obs.provider_health_summary()
        assert health["openai"]["failures"] == 2
        assert health["openai"]["current_status"] == "DOWN"

    def test_avg_latency(self, tmp_path: Path):
        log = tmp_path / "metrics.jsonl"
        obs = ObservabilityLayer(log)

        obs.record(_make_record(provider="anthropic", latency_ms=1000.0))
        obs.record(_make_record(provider="anthropic", latency_ms=2000.0))

        health = obs.provider_health_summary()
        assert health["anthropic"]["avg_latency_ms"] == pytest.approx(1500.0)

    def test_error_field_counts_as_failure(self, tmp_path: Path):
        log = tmp_path / "metrics.jsonl"
        obs = ObservabilityLayer(log)

        obs.record(
            _make_record(provider="anthropic", result="success", error="timeout")
        )

        health = obs.provider_health_summary()
        assert health["anthropic"]["failures"] == 1


class TestDetectCostAnomaly:
    """detect_cost_anomaly() flags 3x+ outliers."""

    def test_no_anomaly(self, tmp_path: Path):
        log = tmp_path / "metrics.jsonl"
        obs = ObservabilityLayer(log)

        obs.record(_make_record(cost_usd=0.05))
        obs.record(_make_record(cost_usd=0.06))

        check = _make_record(cost_usd=0.07)
        result = obs.detect_cost_anomaly(check)
        assert result is None

    def test_anomaly_detected(self, tmp_path: Path):
        log = tmp_path / "metrics.jsonl"
        obs = ObservabilityLayer(log)

        # Record several normal-cost invocations
        obs.record(_make_record(cost_usd=0.05))
        obs.record(_make_record(cost_usd=0.05))
        obs.record(_make_record(cost_usd=0.05))

        # This one is 4x the mean
        expensive = _make_record(cost_usd=0.20)
        result = obs.detect_cost_anomaly(expensive)
        assert result is not None
        assert "anomaly" in result.lower()

    def test_no_history(self, tmp_path: Path):
        log = tmp_path / "metrics.jsonl"
        obs = ObservabilityLayer(log)

        check = _make_record(cost_usd=10.0)
        result = obs.detect_cost_anomaly(check)
        assert result is None

    def test_different_artifact_type_not_compared(self, tmp_path: Path):
        log = tmp_path / "metrics.jsonl"
        obs = ObservabilityLayer(log)

        # Record cheap SAD invocations
        obs.record(_make_record(artifact_type="SAD", cost_usd=0.01))
        obs.record(_make_record(artifact_type="SAD", cost_usd=0.01))

        # Expensive TDD invocation should not be flagged (no TDD history)
        expensive_tdd = _make_record(artifact_type="TDD", cost_usd=1.00)
        result = obs.detect_cost_anomaly(expensive_tdd)
        assert result is None


class TestSpeciesSummary:
    """species_summary() aggregates metrics by agent species."""

    def test_species_summary_aggregates(self, tmp_path: Path):
        log = tmp_path / "metrics.jsonl"
        obs = ObservabilityLayer(log)

        # Record 3 invocations with different species
        r1 = _make_record(provider="anthropic", cost_usd=0.10, latency_ms=1000.0)
        r1.species = AgentSpecies.CODING_HARNESS.value
        obs.record(r1)

        r2 = _make_record(provider="anthropic", cost_usd=0.20, latency_ms=2000.0)
        r2.species = AgentSpecies.DARK_FACTORY.value
        obs.record(r2)

        r3 = _make_record(
            provider="openai", cost_usd=0.05, latency_ms=800.0, result="failure"
        )
        r3.species = AgentSpecies.CODING_HARNESS.value
        obs.record(r3)

        summary = obs.species_summary()

        assert AgentSpecies.CODING_HARNESS.value in summary
        ch = summary[AgentSpecies.CODING_HARNESS.value]
        assert ch["invocations"] == 2
        assert ch["total_cost"] == pytest.approx(0.15, abs=1e-6)
        assert ch["avg_latency"] == pytest.approx(900.0, abs=0.1)
        assert ch["failures"] == 1

        df = summary[AgentSpecies.DARK_FACTORY.value]
        assert df["invocations"] == 1
        assert df["total_cost"] == pytest.approx(0.20, abs=1e-6)
        assert df["failures"] == 0
