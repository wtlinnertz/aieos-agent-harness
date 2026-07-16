"""OpenAI adapter for the AIEOS Agent Harness."""

from __future__ import annotations

import hashlib
import logging
import os
import time

from src.adapters.base import AgentAdapter
from src.models import AgentRequest, AgentResponse, HealthStatus


logger = logging.getLogger(__name__)

# Pricing per 1K tokens (defaults).
#
# G-12: an unpriced model must not silently inherit another model's rate -- see
# the note in adapters/anthropic.py. Unknown models price at 0.0 and log.
_DEFAULT_PRICING: dict[str, dict[str, float]] = {
    "gpt-4o": {"input": 0.005, "output": 0.015},
    "gpt-4o-mini": {"input": 0.00015, "output": 0.0006},
    "gpt-4-turbo": {"input": 0.01, "output": 0.03},
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


class OpenAIAdapter:
    """Adapter for OpenAI Chat Completions API."""

    def __init__(
        self,
        model: str = "gpt-4o",
        max_tokens: int = 8192,
        api_key: str | None = None,
    ) -> None:
        self._model = model
        self._max_tokens = max_tokens
        self._api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        self._client = None  # Lazy init

    @property
    def provider_name(self) -> str:
        return "openai"

    @property
    def model_name(self) -> str:
        return self._model

    def _get_client(self):
        """Lazily initialise the OpenAI client."""
        if self._client is None:
            try:
                import openai
            except ImportError:
                raise ImportError(
                    "openai package required: pip install openai"
                )
            self._client = openai.OpenAI(api_key=self._api_key)
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
        """Build prompt from request fields, call OpenAI, parse response."""
        client = self._get_client()
        system_message, user_message = self._build_messages(request)

        start = time.monotonic()
        response = client.chat.completions.create(
            model=self._model,
            max_tokens=self._max_tokens,
            messages=[
                {"role": "system", "content": system_message},
                {"role": "user", "content": user_message},
            ],
        )
        latency_ms = (time.monotonic() - start) * 1000

        # Extract content
        choice = response.choices[0]
        content = choice.message.content or ""

        tokens_in = response.usage.prompt_tokens
        tokens_out = response.usage.completion_tokens

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
            raw_response={
                "id": response.id,
                "finish_reason": choice.finish_reason,
            },
            # G-7: same signal as Anthropic's stop_reason == "max_tokens",
            # spelled differently. Normalizing here is the whole point of the
            # field -- the convergence loop must not learn provider dialects.
            truncated=(choice.finish_reason == "length"),
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
        estimated_output_tokens = min(
            estimated_input_tokens * 2, self._max_tokens
        )

        pricing = _pricing_for(self._model)
        return round(
            (estimated_input_tokens / 1000) * pricing["input"]
            + (estimated_output_tokens / 1000) * pricing["output"],
            6,
        )
