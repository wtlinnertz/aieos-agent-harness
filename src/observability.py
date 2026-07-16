"""Observability layer for the AIEOS Agent Harness.

Records per-invocation metrics to JSONL and provides aggregation queries.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import mean

from src.models import AgentSpecies, InvocationRecord, LifecycleEvent, RoutingStrategy


def _record_to_dict(record: InvocationRecord) -> dict:
    """Serialise an InvocationRecord to a JSON-safe dict."""
    d = asdict(record)
    # Enums must be stored as their value strings
    d["event"] = record.event.value
    d["strategy"] = record.strategy.value
    return d


def _dict_to_record(d: dict) -> InvocationRecord:
    """Deserialise a dict back into an InvocationRecord."""
    return InvocationRecord(
        timestamp=d["timestamp"],
        artifact_type=d["artifact_type"],
        artifact_id=d["artifact_id"],
        event=LifecycleEvent(d["event"]),
        provider=d["provider"],
        model=d["model"],
        strategy=RoutingStrategy(d["strategy"]),
        tokens_in=d["tokens_in"],
        tokens_out=d["tokens_out"],
        cost_usd=d["cost_usd"],
        latency_ms=d["latency_ms"],
        result=d["result"],
        validation_status=d.get("validation_status"),
        convergence_iteration=d["convergence_iteration"],
        error=d.get("error"),
        species=d.get("species", ""),
    )


class ObservabilityLayer:
    """Per-invocation metrics recording and aggregation."""

    def __init__(self, log_path: Path) -> None:
        self._log_path = log_path

    def record(self, invocation: InvocationRecord) -> None:
        """Append invocation record as a JSON line to the log file."""
        self._log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(_record_to_dict(invocation)) + "\n")

    def read_records(
        self, since: datetime | None = None
    ) -> list[InvocationRecord]:
        """Read all records, optionally filtered by timestamp.

        Records with timestamps at or after *since* are included.
        """
        if not self._log_path.exists():
            return []

        records: list[InvocationRecord] = []
        with open(self._log_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue

                record = _dict_to_record(d)

                if since is not None:
                    try:
                        ts = datetime.fromisoformat(
                            record.timestamp.replace("Z", "+00:00")
                        )
                        if ts < since:
                            continue
                    except (ValueError, AttributeError):
                        continue

                records.append(record)

        return records

    def cost_summary(self, initiative: str | None = None) -> dict:
        """Aggregate cost data.

        Returns:
            {
                "total_cost": float,
                "cost_by_provider": {provider: float, ...},
                "cost_by_artifact_type": {type: float, ...},
                "invocation_count": int,
            }
        """
        records = self.read_records()

        if initiative:
            records = [
                r
                for r in records
                if initiative in r.artifact_id
            ]

        total_cost = 0.0
        cost_by_provider: dict[str, float] = {}
        cost_by_artifact_type: dict[str, float] = {}

        for r in records:
            total_cost += r.cost_usd
            cost_by_provider[r.provider] = (
                cost_by_provider.get(r.provider, 0.0) + r.cost_usd
            )
            cost_by_artifact_type[r.artifact_type] = (
                cost_by_artifact_type.get(r.artifact_type, 0.0) + r.cost_usd
            )

        return {
            "total_cost": round(total_cost, 6),
            "cost_by_provider": {
                k: round(v, 6) for k, v in cost_by_provider.items()
            },
            "cost_by_artifact_type": {
                k: round(v, 6) for k, v in cost_by_artifact_type.items()
            },
            "invocation_count": len(records),
        }

    def provider_health_summary(self) -> dict[str, dict]:
        """Per-provider health metrics.

        Returns:
            {
                provider: {
                    "total_invocations": int,
                    "failures": int,
                    "avg_latency_ms": float,
                    "current_status": str,
                },
                ...
            }
        """
        records = self.read_records()
        providers: dict[str, dict] = {}

        for r in records:
            if r.provider not in providers:
                providers[r.provider] = {
                    "total_invocations": 0,
                    "failures": 0,
                    "latencies": [],
                    "last_result": "",
                }
            p = providers[r.provider]
            p["total_invocations"] += 1
            if r.result == "failure" or r.error:
                p["failures"] += 1
            p["latencies"].append(r.latency_ms)
            p["last_result"] = r.result

        result: dict[str, dict] = {}
        for provider, data in providers.items():
            latencies = data["latencies"]
            avg_latency = round(mean(latencies), 1) if latencies else 0.0
            total = data["total_invocations"]
            failures = data["failures"]

            # Determine current status based on recent failure rate
            if failures == 0:
                status = "OK"
            elif failures / total < 0.5:
                status = "DEGRADED"
            else:
                status = "DOWN"

            result[provider] = {
                "total_invocations": total,
                "failures": failures,
                "avg_latency_ms": avg_latency,
                "current_status": status,
            }

        return result

    def detect_cost_anomaly(
        self,
        invocation: InvocationRecord,
        window_hours: int = 24,
    ) -> str | None:
        """Flag if this invocation's cost exceeds 3x the mean for the same
        artifact_type within the lookback window.

        Returns a warning string if anomalous, None otherwise.
        """
        since = datetime.now(timezone.utc) - timedelta(hours=window_hours)
        records = self.read_records(since=since)

        # Filter to same artifact_type
        same_type = [
            r for r in records if r.artifact_type == invocation.artifact_type
        ]

        if not same_type:
            return None

        mean_cost = mean(r.cost_usd for r in same_type)
        if mean_cost <= 0:
            return None

        if invocation.cost_usd > 3 * mean_cost:
            return (
                f"Cost anomaly: {invocation.artifact_type} invocation cost "
                f"${invocation.cost_usd:.4f} is {invocation.cost_usd / mean_cost:.1f}x "
                f"the {window_hours}h mean of ${mean_cost:.4f}"
            )

        return None

    def species_summary(self) -> dict[str, dict]:
        """Aggregate metrics by agent species.

        Returns:
            {species_name: {invocations, total_cost, avg_latency, failures}}
        """
        records = self.read_records()
        buckets: dict[str, dict] = {}

        for r in records:
            species_key = r.species if r.species else "UNKNOWN"
            if species_key not in buckets:
                buckets[species_key] = {
                    "invocations": 0,
                    "total_cost": 0.0,
                    "latencies": [],
                    "failures": 0,
                }
            b = buckets[species_key]
            b["invocations"] += 1
            b["total_cost"] += r.cost_usd
            b["latencies"].append(r.latency_ms)
            if r.result == "failure" or r.error:
                b["failures"] += 1

        result: dict[str, dict] = {}
        for species_key, data in buckets.items():
            latencies = data["latencies"]
            avg_latency = round(mean(latencies), 1) if latencies else 0.0
            result[species_key] = {
                "invocations": data["invocations"],
                "total_cost": round(data["total_cost"], 6),
                "avg_latency": avg_latency,
                "failures": data["failures"],
            }

        return result
