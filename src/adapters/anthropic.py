"""Anthropic Claude adapter for the AIEOS Agent Harness."""

from __future__ import annotations

import hashlib
import logging
import os
import time

from src.adapters.base import AgentAdapter
from src.models import AgentRequest, AgentResponse, HealthStatus


logger = logging.getLogger(__name__)

# Pricing per 1K tokens (defaults; overridable via config).
#
# G-12: an unpriced model used to fall through to a hardcoded {0.003, 0.015} --
# Sonnet's rate -- so every cost the harness reported for a cheaper model was
# silently inflated. Measured 2026-07-14: it reported $0.0179 for a Haiku call
# whose real cost was ~$0.006, because 3975*0.003 + 401*0.015 is exactly the
# Sonnet arithmetic. Unknown models now price at 0.0 and log a warning: a cost
# of zero is obviously wrong and gets investigated, while a plausible wrong
# number is believed. Never let an estimate impersonate a measurement.
#
# Verify against https://www.anthropic.com/pricing when adding models.
_DEFAULT_PRICING: dict[str, dict[str, float]] = {
    "claude-sonnet-4-20250514": {"input": 0.003, "output": 0.015},
    "claude-opus-4-20250514": {"input": 0.015, "output": 0.075},
    "claude-haiku-3-20250307": {"input": 0.00025, "output": 0.00125},
    "claude-haiku-4-5-20251001": {"input": 0.001, "output": 0.005},
}

_UNKNOWN_PRICING: dict[str, float] = {"input": 0.0, "output": 0.0}


def _pricing_for(model: str) -> dict[str, float]:
    """Pricing for ``model``, or a loud zero if we don't know it (G-12)."""
    pricing = _DEFAULT_PRICING.get(model)
    if pricing is None:
        logger.warning(
            "No pricing entry for model %r; reporting cost 0.0. Add it to "
            "_DEFAULT_PRICING rather than trusting this number.",
            model,
        )
        return _UNKNOWN_PRICING
    return pricing


class AnthropicAdapter:
    """Adapter for Anthropic Claude API."""

    def __init__(
        self,
        model: str = "claude-sonnet-4-20250514",
        max_tokens: int = 8192,
        api_key: str | None = None,
        workspace_id: str | None = None,
    ) -> None:
        self._model = model
        self._max_tokens = max_tokens
        self._api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        # Identity-linked API keys are not scoped to a workspace, so the API
        # requires the caller to name one. Config first, then environment,
        # so the ID never has to be committed. Empty = send no header, which
        # is correct for a plain workspace-scoped key.
        self._workspace_id = (workspace_id or "").strip() or os.environ.get(
            "ANTHROPIC_WORKSPACE_ID", ""
        ).strip()
        self._client = None  # Lazy init

    @property
    def provider_name(self) -> str:
        return "anthropic"

    @property
    def model_name(self) -> str:
        return self._model

    def _get_client(self):
        """Lazily initialise the Anthropic client."""
        if self._client is None:
            try:
                import anthropic
            except ImportError:
                raise ImportError(
                    "anthropic package required: pip install anthropic"
                )
            # The SDK has no workspace parameter (checked against 0.122.0
            # and 1.2.0), so the workspace travels as a default header on
            # every request. No workspace configured = no header, the
            # pre-existing behaviour for workspace-scoped keys.
            headers = (
                {"anthropic-workspace-id": self._workspace_id}
                if self._workspace_id
                else None
            )
            self._client = anthropic.Anthropic(
                api_key=self._api_key, default_headers=headers
            )
        return self._client

    @staticmethod
    def _build_messages(request: AgentRequest) -> tuple[str, str]:
        """Build system and user messages from an AgentRequest.

        System message = spec content.
        User message = prompt content + template + upstream artifacts +
                        correction constraints (if any).
        """
        system_message = request.spec_content

        parts: list[str] = [request.prompt_content]

        # Template
        parts.append("\n\n## Template\n\n" + request.template_content)

        # Upstream artifacts
        for artifact_id, content in request.upstream_artifacts.items():
            parts.append(f"\n\n## {artifact_id}\n\n{content}")

        # Declared inputs (G-3/G-5): principles files + entry brief.
        for input_key, content in request.declared_inputs.items():
            parts.append(f"\n\n## Declared Input — {input_key}\n\n{content}")

        # Current artifact (if present — used for validation requests)
        if request.current_artifact:
            parts.append(
                "\n\n## Current Artifact\n\n" + request.current_artifact
            )

        # Correction constraints
        if request.correction_constraints:
            bullets = "\n".join(
                f"- {c}" for c in request.correction_constraints
            )
            parts.append("\n\n## Correction Constraints\n\n" + bullets)

        user_message = "".join(parts)
        return system_message, user_message

    def invoke(self, request: AgentRequest) -> AgentResponse:
        """Build prompt from request fields, call Claude, parse response."""
        client = self._get_client()
        system_message, user_message = self._build_messages(request)

        # FR-014: pass temperature only when the request pins one (judge
        # calls pin 0.0); None keeps the provider default for generation.
        extra: dict = {}
        if request.temperature is not None:
            extra["temperature"] = request.temperature

        start = time.monotonic()
        try:
            response = client.messages.create(
                model=self._model,
                max_tokens=self._max_tokens,
                system=system_message,
                messages=[{"role": "user", "content": user_message}],
                **extra,
            )
        except Exception as exc:
            # Newer models (claude-sonnet-5 and later) reject the temperature
            # parameter outright ("`temperature` is deprecated for this
            # model", 400). The pin is best-effort: retry once without it.
            # Determinism then rests on the model's default behavior, which
            # the FR-014 calibration stability gate measures EMPIRICALLY
            # (3 identical runs) rather than assumes -- the pin was never
            # the guarantee, the measurement is.
            if (
                "temperature" in extra
                and type(exc).__name__ == "BadRequestError"
                and "temperature" in str(exc)
            ):
                logger.warning(
                    "model %s rejects the temperature parameter; retrying "
                    "without it (determinism unpinned, measured empirically "
                    "by calibration)",
                    self._model,
                )
                extra.pop("temperature")
                response = client.messages.create(
                    model=self._model,
                    max_tokens=self._max_tokens,
                    system=system_message,
                    messages=[{"role": "user", "content": user_message}],
                    **extra,
                )
            else:
                raise
        latency_ms = (time.monotonic() - start) * 1000

        # Extract content text
        content = ""
        for block in response.content:
            if hasattr(block, "text"):
                content += block.text

        tokens_in = response.usage.input_tokens
        tokens_out = response.usage.output_tokens

        # Calculate cost
        pricing = _pricing_for(self._model)
        cost_usd = (
            (tokens_in / 1000) * pricing["input"]
            + (tokens_out / 1000) * pricing["output"]
        )

        # Compute input content hash for provenance
        hash_input = (
            request.spec_content
            + request.template_content
            + request.prompt_content
            + "".join(request.upstream_artifacts.values())
            + "".join(request.declared_inputs.values())
        )
        input_hash = hashlib.sha256(hash_input.encode()).hexdigest()

        return AgentResponse(
            content=content,
            provider=self.provider_name,
            model=self._model,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_usd=round(cost_usd, 6),
            latency_ms=round(latency_ms, 1),
            raw_response={"id": response.id, "stop_reason": response.stop_reason},
            # G-7: normalize Anthropic's spelling of "I ran out of room".
            truncated=(response.stop_reason == "max_tokens"),
            human_author=request.metadata.get("human_author"),
            input_content_hash=input_hash,
        )

    def health(self) -> HealthStatus:
        """Minimal API check — verify the client can be created and key is set."""
        if not self._api_key:
            return HealthStatus.DOWN
        try:
            self._get_client()
            return HealthStatus.OK
        except Exception:
            return HealthStatus.DOWN

    def cost_estimate(self, request: AgentRequest) -> float:
        """Estimate cost based on input character count and model pricing.

        Rough heuristic: 1 token ≈ 4 characters for English text.
        """
        system_message, user_message = self._build_messages(request)
        char_count = len(system_message) + len(user_message)
        estimated_input_tokens = char_count / 4
        # Assume output will be ~2x input for generation, capped at max_tokens
        estimated_output_tokens = min(
            estimated_input_tokens * 2, self._max_tokens
        )

        pricing = _pricing_for(self._model)
        return round(
            (estimated_input_tokens / 1000) * pricing["input"]
            + (estimated_output_tokens / 1000) * pricing["output"],
            6,
        )
