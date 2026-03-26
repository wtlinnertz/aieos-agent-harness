"""Routing engine and circuit breaker for the AIEOS Agent Harness."""

from __future__ import annotations

import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Optional

from src.adapters.base import AgentAdapter
from src.models import AgentRequest, AgentResponse, RoutingStrategy

logger = logging.getLogger(__name__)


class CircuitBreaker:
    """Simple circuit breaker for adapter failures."""

    def __init__(self, max_failures: int = 3, reset_seconds: float = 60.0) -> None:
        self._failures: dict[str, int] = {}
        self._open_since: dict[str, float] = {}
        self._max = max_failures
        self._reset_seconds = reset_seconds

    def record_failure(self, provider: str) -> None:
        self._failures[provider] = self._failures.get(provider, 0) + 1
        if self._failures[provider] >= self._max:
            self._open_since[provider] = time.monotonic()

    def record_success(self, provider: str) -> None:
        self._failures.pop(provider, None)
        self._open_since.pop(provider, None)

    def is_open(self, provider: str) -> bool:
        opened_at = self._open_since.get(provider)
        if opened_at is None:
            return False
        if time.monotonic() - opened_at >= self._reset_seconds:
            # Half-open: allow a retry after reset period
            self._open_since.pop(provider, None)
            self._failures.pop(provider, None)
            return False
        return True


class RoutingEngine:
    """Routes requests through adapters using specified strategies."""

    def __init__(self, circuit_breaker: Optional[CircuitBreaker] = None) -> None:
        self._cb = circuit_breaker or CircuitBreaker()

    def route(
        self,
        strategy: RoutingStrategy,
        adapters: list[AgentAdapter],
        request: AgentRequest,
        config: dict,
    ) -> AgentResponse:
        """Route request through adapters using specified strategy."""
        dispatch = {
            RoutingStrategy.PARALLEL_CONSENSUS: self._parallel_consensus,
            RoutingStrategy.PIPELINE: self._pipeline,
            RoutingStrategy.FALLBACK: self._fallback,
            RoutingStrategy.COST_AWARE: self._cost_aware,
        }
        handler = dispatch.get(strategy)
        if handler is None:
            raise ValueError(f"Unknown routing strategy: {strategy}")
        return handler(adapters, request, config)

    def _parallel_consensus(
        self,
        adapters: list[AgentAdapter],
        request: AgentRequest,
        config: dict,
    ) -> AgentResponse:
        """Fan out to all adapters in parallel via ThreadPoolExecutor.

        Collect responses. Check agreement: if fraction of successful responses
        >= threshold (config.get('threshold', 0.67)), return first successful.
        If below threshold, raise ValueError with disagreement details.

        Agreement = all responses have same validation status or similar
        content length (within 20%).
        """
        threshold = config.get("threshold", 0.67)
        responses: list[AgentResponse] = []
        errors: list[str] = []

        with ThreadPoolExecutor(max_workers=len(adapters)) as executor:
            futures = {
                executor.submit(adapter.invoke, request): adapter
                for adapter in adapters
            }
            for future in as_completed(futures):
                adapter = futures[future]
                try:
                    responses.append(future.result())
                except Exception as exc:
                    errors.append(
                        f"{adapter.provider_name}: {exc}"
                    )

        if not responses:
            raise RuntimeError(
                f"All adapters failed in parallel consensus: {errors}"
            )

        # Check agreement: content lengths within 20% of each other
        total = len(responses) + len(errors)
        agreement_count = self._count_agreeing(responses)
        agreement_fraction = agreement_count / total if total > 0 else 0.0

        if agreement_fraction >= threshold:
            return responses[0]

        raise ValueError(
            f"Parallel consensus disagreement: {agreement_count}/{total} "
            f"agreed (threshold={threshold}). Errors: {errors}"
        )

    @staticmethod
    def _count_agreeing(responses: list[AgentResponse]) -> int:
        """Count responses that agree with each other.

        Agreement means content lengths are within 20% of the median length.
        """
        if not responses:
            return 0
        if len(responses) == 1:
            return 1

        lengths = [len(r.content) for r in responses]
        median_length = sorted(lengths)[len(lengths) // 2]
        if median_length == 0:
            return sum(1 for ln in lengths if ln == 0)

        tolerance = 0.20
        agreeing = sum(
            1
            for ln in lengths
            if abs(ln - median_length) / median_length <= tolerance
        )
        return agreeing

    def _pipeline(
        self,
        adapters: list[AgentAdapter],
        request: AgentRequest,
        config: dict,
    ) -> AgentResponse:
        """Sequential chain: invoke adapter[0], put its response content as
        current_artifact in a new request, invoke adapter[1], etc.

        Return final response. If any step fails, raise.
        """
        if not adapters:
            raise ValueError("Pipeline requires at least one adapter")

        current_request = request
        response: Optional[AgentResponse] = None

        for i, adapter in enumerate(adapters):
            try:
                response = adapter.invoke(current_request)
            except Exception as exc:
                raise RuntimeError(
                    f"Pipeline step {i} ({adapter.provider_name}) failed: {exc}"
                ) from exc

            # Build next request with output as current_artifact
            if i < len(adapters) - 1:
                current_request = AgentRequest(
                    artifact_type=request.artifact_type,
                    event=request.event,
                    spec_content=request.spec_content,
                    template_content=request.template_content,
                    prompt_content=request.prompt_content,
                    upstream_artifacts=request.upstream_artifacts,
                    current_artifact=response.content,
                    correction_constraints=request.correction_constraints,
                    metadata=request.metadata,
                )

        assert response is not None
        return response

    def _fallback(
        self,
        adapters: list[AgentAdapter],
        request: AgentRequest,
        config: dict,
    ) -> AgentResponse:
        """Try adapter[0]. If fails (exception or circuit breaker open),
        try adapter[1], etc.

        Record failures in circuit breaker. Return first successful response.
        If all fail, raise RuntimeError with all errors.
        """
        errors: list[str] = []

        for adapter in adapters:
            provider = adapter.provider_name
            if self._cb.is_open(provider):
                errors.append(f"{provider}: circuit breaker open")
                continue
            try:
                response = adapter.invoke(request)
                self._cb.record_success(provider)
                return response
            except Exception as exc:
                self._cb.record_failure(provider)
                errors.append(f"{provider}: {exc}")

        raise RuntimeError(
            f"All adapters failed in fallback chain: {'; '.join(errors)}"
        )

    def _cost_aware(
        self,
        adapters: list[AgentAdapter],
        request: AgentRequest,
        config: dict,
    ) -> AgentResponse:
        """Sort adapters by cost_estimate(request) ascending.

        If config has 'min_tier', filter to adapters meeting minimum.
        Invoke cheapest available (circuit breaker not open).
        Fallback to next cheapest if invocation fails.
        """
        min_tier = config.get("min_tier")

        # Build cost-sorted list
        costed: list[tuple[float, AgentAdapter]] = []
        for adapter in adapters:
            cost = adapter.cost_estimate(request)
            if min_tier is not None and cost < min_tier:
                continue
            costed.append((cost, adapter))

        costed.sort(key=lambda x: x[0])

        errors: list[str] = []
        for cost, adapter in costed:
            provider = adapter.provider_name
            if self._cb.is_open(provider):
                errors.append(f"{provider}: circuit breaker open")
                continue
            try:
                response = adapter.invoke(request)
                self._cb.record_success(provider)
                return response
            except Exception as exc:
                self._cb.record_failure(provider)
                errors.append(f"{provider}: {exc}")

        raise RuntimeError(
            f"All adapters failed in cost-aware routing: {'; '.join(errors)}"
        )
