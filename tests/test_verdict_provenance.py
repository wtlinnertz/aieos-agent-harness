"""FR-014 slice 1 — verdict provenance + judge determinism (Phase 1: tests).

WHY THIS FILE EXISTS. G-9's receipts: the sad-validator failed a gate while
quoting the spec's own exemption, and the same prompt + model returned
opposite verdicts on one hard gate back to back. Calibration (FR-014,
[[AIEOS v1.4 - FR-014 Build Spec]]) needs two preconditions this slice
delivers:

1. DETERMINISM — judge calls run at temperature 0. Without it, the
   calibration stability gate (3 runs, no gate flip) measures provider
   sampling noise, not the judge.
2. PROVENANCE — every verdict records which judge produced it: the sha256
   of the validator prompt content, the provider, the model, and the
   temperature. Without it a verdict is not traceable to a judge version
   and calibration data must be reconstructed instead of read.

Plus the G-10 half-fix: run_artifact and run_artifact_lifecycle currently
DISCARD state.ledger, so an escalation reports no reason and no verdict
survives the run. Verdicts persist to the initiative's .aieos/verdicts.jsonl
(append-only, one JSON object per line, utf-8) — ephemeral runtime state
lives in the sidecar, same placement rule as the lock.

Run: pytest tests/test_verdict_provenance.py
Expected at Phase 1: ALL FAIL (AgentRequest has no temperature field, ledger
entries carry no provenance, nothing writes verdicts.jsonl). If any test
passes before implementation, the test is wrong, not the code.
"""

import hashlib
import json

from src.adapters.mock import MockAdapter
from src.convergence import ConvergenceLoop
from src.driver import HarnessDriver
from src.models import AgentRequest, LifecycleEvent, LifecycleResult


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
FAIL_JSON = json.dumps(
    {
        "status": "FAIL",
        "summary": "no",
        "hard_gates": {"g1": "FAIL"},
        "blocking_issues": [
            {"gate": "g1", "description": "missing section", "location": "s1"}
        ],
        "warnings": [],
        "completeness_score": 40,
    }
)

VALIDATOR_PROMPT = "validate"
VALIDATOR_SHA = hashlib.sha256(VALIDATOR_PROMPT.encode("utf-8")).hexdigest()


class CapturingAdapter(MockAdapter):
    """MockAdapter that records every AgentRequest it receives."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.requests: list[AgentRequest] = []

    def invoke(self, request):
        self.requests.append(request)
        return super().invoke(request)


def _requests():
    gen = AgentRequest(
        artifact_type="SAD",
        event=LifecycleEvent.PRE_GENERATION,
        spec_content="spec",
        template_content="tmpl",
        prompt_content="prompt",
        upstream_artifacts={},
        current_artifact=None,
        correction_constraints=[],
        metadata={"artifact_id": "SAD-TEST-001"},
    )
    val = AgentRequest(
        artifact_type="SAD",
        event=LifecycleEvent.PRE_VALIDATION,
        spec_content="spec",
        template_content="",
        prompt_content=VALIDATOR_PROMPT,
        upstream_artifacts={},
        current_artifact=None,
        correction_constraints=[],
        metadata={"artifact_id": "SAD-TEST-001"},
    )
    return gen, val


def _fake_kit(aieos_root, artifact_type):
    """Minimal kit tree for run_artifact resolution, WITH a validator file so
    the provenance sha in verdicts.jsonl is the validator's, not the prompt's."""
    kit = aieos_root / "aieos-eek"
    (kit / "docs" / "specs").mkdir(parents=True, exist_ok=True)
    (kit / "docs" / "artifacts").mkdir(parents=True, exist_ok=True)
    (kit / "docs" / "prompts").mkdir(parents=True, exist_ok=True)
    (kit / "docs" / "validators").mkdir(parents=True, exist_ok=True)
    t = artifact_type.lower()
    (kit / "docs" / "specs" / f"{t}-spec.md").write_text(
        f"# {artifact_type} spec", encoding="utf-8"
    )
    (kit / "docs" / "artifacts" / f"{t}-template.md").write_text(
        "template", encoding="utf-8"
    )
    (kit / "docs" / "prompts" / f"{t}-prompt.md").write_text(
        "prompt", encoding="utf-8"
    )
    (kit / "docs" / "validators" / f"{t}-validator.md").write_text(
        VALIDATOR_PROMPT, encoding="utf-8"
    )


class TestJudgeCallsPinTemperatureZero:
    """Determinism is a precondition for the calibration stability gate.

    The generator keeps the provider default (creativity is its job). The
    judge does not get a vote about it: the ConvergenceLoop pins validation
    and lens requests to 0.0 STRUCTURALLY, regardless of what the caller put
    on the incoming request — the loop is the one place that knows a call is
    a judge call.
    """

    def test_validation_invoke_receives_temperature_zero(self):
        gen = CapturingAdapter()
        val = CapturingAdapter(preset_responses={"SAD": PASS_JSON})
        loop = ConvergenceLoop(gen, val)
        gen_req, val_req = _requests()
        loop.run(gen_req, val_req)
        assert val.requests, "validate adapter was never invoked"
        assert val.requests[0].temperature == 0.0

    def test_generation_invoke_temperature_is_unpinned(self):
        gen = CapturingAdapter()
        val = CapturingAdapter(preset_responses={"SAD": PASS_JSON})
        loop = ConvergenceLoop(gen, val)
        gen_req, val_req = _requests()
        loop.run(gen_req, val_req)
        # Field must exist and default to None (provider default), never 0.0:
        # pinning the GENERATOR would be a behaviour change nobody ratified.
        assert getattr(gen.requests[0], "temperature", "MISSING") is None

    def test_lens_invoke_receives_temperature_zero(self):
        gen = CapturingAdapter()
        val = CapturingAdapter(preset_responses={"SAD": PASS_JSON})
        lens = CapturingAdapter(preset_responses={"SAD": PASS_JSON})
        loop = ConvergenceLoop(gen, val, lens_adapters={"security": lens})
        gen_req, val_req = _requests()
        loop.run(gen_req, val_req)
        assert lens.requests, "lens adapter was never invoked"
        assert lens.requests[0].temperature == 0.0


class TestLedgerCarriesProvenance:
    """A verdict without provenance is not calibration data.

    The sha is of the PROMPT CONTENT THE JUDGE ACTUALLY RECEIVED
    (validation_request.prompt_content), not of a file path — the same
    content-addressed discipline the freeze authority uses for artifacts.
    """

    def test_validation_entry_provenance(self):
        gen = CapturingAdapter()
        val = CapturingAdapter(
            provider_name="prov-x",
            model_name="model-y",
            preset_responses={"SAD": PASS_JSON},
        )
        loop = ConvergenceLoop(gen, val)
        gen_req, val_req = _requests()
        _, _, state = loop.run(gen_req, val_req)
        prov = state.ledger[0]["provenance"]
        assert prov["validator_prompt_sha256"] == VALIDATOR_SHA
        assert prov["provider"] == "prov-x"
        assert prov["model"] == "model-y"
        assert prov["temperature"] == 0.0

    def test_lens_entry_provenance(self):
        # Lens reviewers are judges too — slice 2 calibrates them eventually,
        # so their verdicts need the same traceability now.
        gen = CapturingAdapter()
        val = CapturingAdapter(preset_responses={"SAD": PASS_JSON})
        lens = CapturingAdapter(
            provider_name="lens-prov",
            model_name="lens-model",
            preset_responses={"SAD": PASS_JSON},
        )
        loop = ConvergenceLoop(gen, val, lens_adapters={"security": lens})
        gen_req, val_req = _requests()
        _, _, state = loop.run(gen_req, val_req)
        lens_entries = [e for e in state.ledger if e.get("lens") == "security"]
        assert lens_entries, "lens verdict never reached the ledger"
        prov = lens_entries[0]["provenance"]
        assert prov["provider"] == "lens-prov"
        assert prov["model"] == "lens-model"
        assert prov["temperature"] == 0.0
        assert prov["validator_prompt_sha256"] == hashlib.sha256(
            lens.requests[0].prompt_content.encode("utf-8")
        ).hexdigest()


class TestVerdictsPersistedToDisk:
    """G-10 half-fix: the ledger survives the run instead of dying with it.

    .aieos/verdicts.jsonl in the initiative — append-only, one JSON object
    per line, utf-8. Ephemeral runtime record, so the sidecar is the right
    home (same placement rule as the FR-019 lock). The public
    LifecycleResult contract is UNCHANGED — the dark factory's
    LifecycleResult[data["result"]] seam must not notice this slice.
    """

    def _read_verdicts(self, initiative):
        path = initiative / ".aieos" / "verdicts.jsonl"
        assert path.exists(), "verdicts.jsonl was not written"
        lines = path.read_text(encoding="utf-8").strip().splitlines()
        return [json.loads(line) for line in lines]

    def test_lifecycle_persists_verdicts(self, tmp_path):
        gen = MockAdapter()
        val = MockAdapter(preset_responses={"SAD": PASS_JSON})
        driver = HarnessDriver(tmp_path, gen, val)
        gen_req, val_req = _requests()
        result = driver.run_artifact_lifecycle(gen_req, val_req)
        assert result == LifecycleResult.CONVERGED
        records = self._read_verdicts(tmp_path)
        assert len(records) == 1
        rec = records[0]
        assert rec["artifact_type"] == "SAD"
        assert rec["status"] == "PASS"
        assert rec["provenance"]["validator_prompt_sha256"] == VALIDATOR_SHA
        assert "T" in rec["timestamp"]  # ISO 8601

    def test_escalation_verdicts_carry_blocking_reasons(self, tmp_path):
        # The exact G-10 symptom: budget exhausted, escalation reported, and
        # no reason recorded anywhere. Every persisted FAIL verdict must carry
        # its blocking_issues.
        gen = MockAdapter()
        val = MockAdapter(preset_responses={"SAD": FAIL_JSON})
        driver = HarnessDriver(tmp_path, gen, val, max_iterations=2)
        gen_req, val_req = _requests()
        result = driver.run_artifact_lifecycle(gen_req, val_req)
        assert result == LifecycleResult.ESCALATION_NEEDED
        records = self._read_verdicts(tmp_path)
        assert len(records) == 2
        for rec in records:
            assert rec["status"] == "FAIL"
            assert rec["blocking_issues"], "escalation verdict carries no reason"

    def test_run_artifact_persists_verdicts(self, tmp_path):
        # THROUGH run_artifact — the conductor-facing path G-10 named. The
        # provenance sha must be the VALIDATOR file's content hash, proving
        # the judge identity survives kit-file resolution.
        aieos_root = tmp_path / "aieos"
        aieos_root.mkdir()
        _fake_kit(aieos_root, "PRD")
        initiative = tmp_path / "init"
        initiative.mkdir()
        driver = HarnessDriver(
            initiative,
            MockAdapter(),
            MockAdapter(preset_responses={"PRD": PASS_JSON}),
            aieos_root=aieos_root,
        )
        assert driver.run_artifact("PRD") == LifecycleResult.CONVERGED
        records = self._read_verdicts(initiative)
        assert records[0]["artifact_type"] == "PRD"
        assert (
            records[0]["provenance"]["validator_prompt_sha256"] == VALIDATOR_SHA
        )

    def test_verdicts_append_across_runs(self, tmp_path):
        gen = MockAdapter()
        val = MockAdapter(preset_responses={"SAD": PASS_JSON})
        driver = HarnessDriver(tmp_path, gen, val)
        gen_req, val_req = _requests()
        driver.run_artifact_lifecycle(gen_req, val_req)
        driver.run_artifact_lifecycle(gen_req, val_req)
        assert len(self._read_verdicts(tmp_path)) == 2


class TestAdaptersHonorTemperature:
    """The loop pinning 0.0 is fiction unless the real adapters pass it.

    Fake clients injected at the lazy-init seam capture the provider-call
    kwargs. The rule both ways: pinned temperature reaches the API call,
    None OMITS the parameter entirely (provider default, not an explicit 1.0
    or a surprise 0.0).
    """

    class _FakeAnthropicClient:
        class _Block:
            text = "ok"

        class _Usage:
            input_tokens = 10
            output_tokens = 20

        class _Response:
            def __init__(self):
                self.content = [TestAdaptersHonorTemperature._FakeAnthropicClient._Block()]
                self.usage = TestAdaptersHonorTemperature._FakeAnthropicClient._Usage()
                self.id = "fake-id"
                self.stop_reason = "end_turn"

        def __init__(self):
            self.calls: list[dict] = []
            outer = self

            class _Messages:
                def create(self, **kwargs):
                    outer.calls.append(kwargs)
                    return outer._Response()

            self.messages = _Messages()

    class _FakeOpenAIClient:
        class _Message:
            content = "ok"

        class _Choice:
            def __init__(self):
                self.message = TestAdaptersHonorTemperature._FakeOpenAIClient._Message()
                self.finish_reason = "stop"

        class _Usage:
            prompt_tokens = 10
            completion_tokens = 20

        class _Response:
            def __init__(self):
                self.choices = [TestAdaptersHonorTemperature._FakeOpenAIClient._Choice()]
                self.usage = TestAdaptersHonorTemperature._FakeOpenAIClient._Usage()
                self.id = "fake-id"

        def __init__(self):
            self.calls: list[dict] = []
            outer = self

            class _Completions:
                def create(self, **kwargs):
                    outer.calls.append(kwargs)
                    return outer._Response()

            class _Chat:
                completions = _Completions()

            self.chat = _Chat()

    def _request(self, temperature):
        gen_req, _ = _requests()
        return AgentRequest(
            artifact_type=gen_req.artifact_type,
            event=gen_req.event,
            spec_content=gen_req.spec_content,
            template_content=gen_req.template_content,
            prompt_content=gen_req.prompt_content,
            upstream_artifacts={},
            current_artifact=None,
            correction_constraints=[],
            metadata={},
            temperature=temperature,
        )

    def test_anthropic_passes_pinned_temperature(self):
        from src.adapters.anthropic import AnthropicAdapter

        adapter = AnthropicAdapter(api_key="test")
        fake = self._FakeAnthropicClient()
        adapter._client = fake
        adapter.invoke(self._request(0.0))
        assert fake.calls[0]["temperature"] == 0.0

    def test_anthropic_omits_temperature_when_unpinned(self):
        from src.adapters.anthropic import AnthropicAdapter

        adapter = AnthropicAdapter(api_key="test")
        fake = self._FakeAnthropicClient()
        adapter._client = fake
        adapter.invoke(self._request(None))
        assert "temperature" not in fake.calls[0]

    def test_openai_passes_pinned_temperature(self):
        from src.adapters.openai import OpenAIAdapter

        adapter = OpenAIAdapter(api_key="test")
        fake = self._FakeOpenAIClient()
        adapter._client = fake
        adapter.invoke(self._request(0.0))
        assert fake.calls[0]["temperature"] == 0.0

    def test_openai_omits_temperature_when_unpinned(self):
        from src.adapters.openai import OpenAIAdapter

        adapter = OpenAIAdapter(api_key="test")
        fake = self._FakeOpenAIClient()
        adapter._client = fake
        adapter.invoke(self._request(None))
        assert "temperature" not in fake.calls[0]


class TestAnthropicTemperatureDeprecatedFallback:
    """claude-sonnet-5 and later reject the temperature parameter (400,
    "`temperature` is deprecated for this model" — hit live 2026-08-16).
    The adapter retries once without it. The FR-014 stability gate never
    trusted the pin — it measures flips across 3 identical runs — so the
    fallback preserves the design: pin when the model allows, measure
    regardless.
    """

    class _RejectingClient:
        class _Block:
            text = "ok"

        class _Usage:
            input_tokens = 10
            output_tokens = 20

        class _Response:
            def __init__(self):
                outer = TestAnthropicTemperatureDeprecatedFallback._RejectingClient
                self.content = [outer._Block()]
                self.usage = outer._Usage()
                self.id = "fake-id"
                self.stop_reason = "end_turn"

        def __init__(self):
            self.calls: list[dict] = []
            outer = self

            class BadRequestError(Exception):
                """Name must match anthropic.BadRequestError for the check."""

            self._error_cls = BadRequestError

            class _Messages:
                def create(self, **kwargs):
                    outer.calls.append(kwargs)
                    if "temperature" in kwargs:
                        raise outer._error_cls(
                            "Error code: 400 - `temperature` is deprecated for this model."
                        )
                    return outer._Response()

            self.messages = _Messages()

    def _request(self, temperature):
        return AgentRequest(
            artifact_type="SAD",
            event=LifecycleEvent.PRE_VALIDATION,
            spec_content="s",
            template_content="t",
            prompt_content="p",
            upstream_artifacts={},
            current_artifact="a",
            correction_constraints=[],
            metadata={},
            temperature=temperature,
        )

    def test_retries_without_temperature_on_deprecation_400(self):
        from src.adapters.anthropic import AnthropicAdapter

        adapter = AnthropicAdapter(api_key="test")
        fake = self._RejectingClient()
        adapter._client = fake
        response = adapter.invoke(self._request(0.0))
        assert len(fake.calls) == 2, "expected pinned attempt + one retry"
        assert fake.calls[0]["temperature"] == 0.0
        assert "temperature" not in fake.calls[1]
        assert response.content == "ok"

    def test_unpinned_request_never_retries(self):
        from src.adapters.anthropic import AnthropicAdapter

        adapter = AnthropicAdapter(api_key="test")
        fake = self._RejectingClient()
        adapter._client = fake
        adapter.invoke(self._request(None))
        assert len(fake.calls) == 1

    def test_other_errors_still_raise(self):
        import pytest

        from src.adapters.anthropic import AnthropicAdapter

        adapter = AnthropicAdapter(api_key="test")
        fake = self._RejectingClient()
        adapter._client = fake

        def _boom(**kwargs):
            raise RuntimeError("nope")

        fake.messages.create = _boom
        with pytest.raises(RuntimeError):
            adapter.invoke(self._request(0.0))
