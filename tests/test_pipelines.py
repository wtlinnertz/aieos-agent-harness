"""Tests for pre-built harness pipelines."""

import json
import subprocess
import textwrap

import pytest
from unittest.mock import patch, MagicMock

from src.adapters.mock import MockAdapter
from src.pipelines import (
    ORDVerificationPipeline,
    CodebaseAnalysisPipeline,
    HealthReviewPipeline,
    PipelineResult,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_subprocess_result(returncode=0, stdout="ok", stderr=""):
    """Build a mock subprocess.CompletedProcess."""
    result = MagicMock(spec=subprocess.CompletedProcess)
    result.returncode = returncode
    result.stdout = stdout
    result.stderr = stderr
    return result


# ===========================================================================
# ORDVerificationPipeline
# ===========================================================================


class TestORDVerificationPipeline:
    """Tests for ORD operational readiness verification."""

    @patch("subprocess.run")
    def test_all_checks_pass(self, mock_run):
        """When test/build/lint all exit 0, status is 'pass'."""
        mock_run.return_value = _make_subprocess_result(returncode=0, stdout="all passed")

        adapter = MockAdapter()
        pipeline = ORDVerificationPipeline(
            ai_adapter=adapter,
            test_command="pytest -v",
            build_command="make build",
            lint_command="ruff check .",
        )

        result = pipeline.verify(ord_content="# ORD content")

        assert result.status == "pass"
        assert result.details["all_tool_checks_passed"] is True
        assert len(result.details["checks"]) == 3
        assert mock_run.call_count == 3

    @patch("subprocess.run")
    def test_test_failure(self, mock_run):
        """When tests exit 1, status is 'fail'."""
        # First call (tests) fails, rest pass
        mock_run.side_effect = [
            _make_subprocess_result(returncode=1, stdout="FAILED 2 tests"),
            _make_subprocess_result(returncode=0, stdout="build ok"),
        ]

        adapter = MockAdapter()
        pipeline = ORDVerificationPipeline(
            ai_adapter=adapter,
            test_command="pytest -v",
            build_command="make build",
        )

        result = pipeline.verify(ord_content="# ORD")

        assert result.status == "fail"
        assert result.details["all_tool_checks_passed"] is False

    @patch("subprocess.run")
    def test_ai_receives_tool_output(self, mock_run):
        """AI adapter request includes tool stdout in upstream_artifacts."""
        mock_run.return_value = _make_subprocess_result(
            returncode=0, stdout="12 tests passed"
        )

        adapter = MockAdapter()
        pipeline = ORDVerificationPipeline(ai_adapter=adapter)

        pipeline.verify(ord_content="# ORD content")

        # MockAdapter records calls; check the request sent to invoke
        assert len(adapter.call_history) == 1
        call_name, call_args = adapter.call_history[0]
        assert call_name == "invoke"
        request = call_args[0]
        assert "12 tests passed" in request.upstream_artifacts["tool_results"]

    @patch("subprocess.run")
    def test_optional_build_lint(self, mock_run):
        """Only test_command is required; build and lint are optional."""
        mock_run.return_value = _make_subprocess_result(returncode=0, stdout="ok")

        adapter = MockAdapter()
        pipeline = ORDVerificationPipeline(ai_adapter=adapter)

        result = pipeline.verify(ord_content="# ORD")

        # Only one subprocess call (the test command)
        assert mock_run.call_count == 1
        assert len(result.details["checks"]) == 1
        assert result.details["checks"][0]["check"] == "test_suite"

    @patch("subprocess.run")
    def test_command_timeout(self, mock_run):
        """Subprocess timeout is captured as exit -1."""
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="pytest", timeout=300)

        adapter = MockAdapter()
        pipeline = ORDVerificationPipeline(ai_adapter=adapter)

        result = pipeline.verify(ord_content="# ORD")

        assert result.status == "fail"
        check = result.details["checks"][0]
        assert check["exit_code"] == -1
        assert "timed out" in check["stderr"]

    @patch("subprocess.run")
    def test_result_has_tool_and_ai(self, mock_run):
        """PipelineResult contains both tool_output and ai_assessment."""
        mock_run.return_value = _make_subprocess_result(returncode=0, stdout="ok")

        adapter = MockAdapter(
            preset_responses={"ORD-verification": "Readiness confirmed."}
        )
        pipeline = ORDVerificationPipeline(ai_adapter=adapter)

        result = pipeline.verify(ord_content="# ORD")

        assert isinstance(result, PipelineResult)
        assert result.tool_output != ""
        assert result.ai_assessment == "Readiness confirmed."
        assert result.status == "pass"


# ===========================================================================
# CodebaseAnalysisPipeline
# ===========================================================================


class TestCodebaseAnalysisPipeline:
    """Tests for brownfield codebase analysis."""

    @patch("subprocess.run")
    def test_runs_configured_tools(self, mock_run):
        """SAST and lint commands both run and appear in result."""
        mock_run.return_value = _make_subprocess_result(
            returncode=0, stdout="no findings"
        )

        adapter = MockAdapter()
        pipeline = CodebaseAnalysisPipeline(
            ai_adapter=adapter,
            sast_command="bandit -r src/",
            lint_command="ruff check .",
        )

        result = pipeline.analyze(initiative_description="test initiative")

        # sast + lint + file_structure = 3 subprocess calls
        assert mock_run.call_count == 3
        assert "no findings" in result.tool_output
        assert result.status == "complete"

    @patch("subprocess.run")
    def test_ai_receives_all_findings(self, mock_run):
        """AI request upstream_artifacts contains combined tool results."""
        mock_run.side_effect = [
            _make_subprocess_result(returncode=0, stdout="SAST: 0 issues"),
            _make_subprocess_result(returncode=0, stdout="Lint: clean"),
            _make_subprocess_result(returncode=0, stdout="src/main.py"),
        ]

        adapter = MockAdapter()
        pipeline = CodebaseAnalysisPipeline(
            ai_adapter=adapter,
            sast_command="bandit -r src/",
            lint_command="ruff check .",
        )

        pipeline.analyze()

        assert len(adapter.call_history) == 1
        request = adapter.call_history[0][1][0]
        tool_results = request.upstream_artifacts["tool_results"]
        assert "SAST: 0 issues" in tool_results
        assert "Lint: clean" in tool_results

    @patch("subprocess.run")
    def test_no_tools_configured(self, mock_run):
        """With no tool commands, only file structure is collected."""
        mock_run.return_value = _make_subprocess_result(
            returncode=0, stdout="src/app.py"
        )

        adapter = MockAdapter()
        pipeline = CodebaseAnalysisPipeline(ai_adapter=adapter)

        result = pipeline.analyze()

        # Only the find command for file structure
        assert mock_run.call_count == 1
        assert result.status == "complete"
        assert "file_structure" in result.tool_output

    @patch("subprocess.run")
    def test_result_status_complete(self, mock_run):
        """analyze() always returns status 'complete'."""
        mock_run.return_value = _make_subprocess_result(
            returncode=1, stdout="errors found"
        )

        adapter = MockAdapter()
        pipeline = CodebaseAnalysisPipeline(
            ai_adapter=adapter, lint_command="ruff check ."
        )

        result = pipeline.analyze()

        # Even when tools report issues, pipeline status is "complete"
        assert result.status == "complete"


# ===========================================================================
# HealthReviewPipeline
# ===========================================================================


class TestHealthReviewPipeline:
    """Tests for RRK health review."""

    def test_reads_metrics_file(self, tmp_path):
        """Metrics JSONL file is parsed into a summary."""
        metrics_file = tmp_path / "metrics.jsonl"
        lines = [
            json.dumps({"timestamp": "2026-03-27T10:00:00Z", "cost_usd": 0.01, "latency_ms": 100, "result": "success"}),
            json.dumps({"timestamp": "2026-03-27T10:05:00Z", "cost_usd": 0.02, "latency_ms": 200, "result": "failure"}),
            json.dumps({"timestamp": "2026-03-27T10:10:00Z", "cost_usd": 0.01, "latency_ms": 150, "result": "success"}),
        ]
        metrics_file.write_text("\n".join(lines))

        adapter = MockAdapter()
        pipeline = HealthReviewPipeline(
            ai_adapter=adapter,
            metrics_path=str(metrics_file),
        )

        result = pipeline.review()

        assert "observability_metrics" in result.tool_output
        # Verify the summary was parsed (should contain entry count)
        assert '"entries": 3' in result.tool_output
        assert '"failure_count": 1' in result.tool_output

    @patch("subprocess.run")
    def test_health_commands_run(self, mock_run):
        """Health check commands are executed and included in output."""
        mock_run.return_value = _make_subprocess_result(
            returncode=0, stdout="HTTP/1.1 200 OK"
        )

        adapter = MockAdapter()
        pipeline = HealthReviewPipeline(
            ai_adapter=adapter,
            health_commands={
                "api": "curl -s http://localhost:8080/health",
                "db": "pg_isready",
            },
        )

        result = pipeline.review()

        assert mock_run.call_count == 2
        assert "health_api" in result.tool_output
        assert "health_db" in result.tool_output
        assert "200 OK" in result.tool_output

    def test_slo_baselines_in_prompt(self):
        """SLO baselines appear in the AI prompt content."""
        adapter = MockAdapter()
        pipeline = HealthReviewPipeline(
            ai_adapter=adapter,
            slo_baselines={
                "p99_latency_ms": 500.0,
                "error_rate": 0.01,
            },
        )

        pipeline.review(srp_content="# SRP content")

        assert len(adapter.call_history) == 1
        request = adapter.call_history[0][1][0]
        assert "p99_latency_ms: 500.0" in request.prompt_content
        assert "error_rate: 0.01" in request.prompt_content

    def test_prior_rhr_in_context(self):
        """Prior RHR content is passed to AI in upstream_artifacts."""
        adapter = MockAdapter()
        pipeline = HealthReviewPipeline(ai_adapter=adapter)

        prior = "## Prior RHR\nStatus: healthy\nPeriod: 2026-03-20 to 2026-03-27"
        pipeline.review(prior_rhr_content=prior)

        assert len(adapter.call_history) == 1
        request = adapter.call_history[0][1][0]
        assert request.upstream_artifacts["prior_rhr"] == prior

    def test_empty_metrics(self, tmp_path):
        """Missing metrics file is handled gracefully."""
        adapter = MockAdapter()
        pipeline = HealthReviewPipeline(
            ai_adapter=adapter,
            metrics_path=str(tmp_path / "nonexistent.jsonl"),
        )

        result = pipeline.review()

        assert result.status == "complete"
        assert "metrics file not found" in result.tool_output
