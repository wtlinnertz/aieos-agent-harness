"""Pre-built harness pipelines for common AIEOS lifecycle integrations.

Each pipeline configures a specific Tool->AI chain using the harness's
existing routing engine PIPELINE strategy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from src.models import AgentRequest, AgentResponse, LifecycleEvent
from src.adapters.base import AgentAdapter


@dataclass
class PipelineResult:
    """Result from a pipeline execution."""

    tool_output: str  # Raw tool output
    ai_assessment: str  # AI interpretation
    status: str  # "pass", "fail", "warning"
    details: dict = field(default_factory=dict)


class ORDVerificationPipeline:
    """Operational Readiness verification mixing deterministic + AI checks.

    Step 1 (Tool): Run deterministic checks:
      - Test suite passes (exit code 0)
      - Build is clean (exit code 0)
      - Lint passes (exit code 0)

    Step 2 (AI): Interpret results + assess readiness:
      - Parse test/build/lint output
      - Compare against ORD spec requirements
      - Assess operational readiness holistically
    """

    def __init__(
        self,
        ai_adapter: AgentAdapter,
        test_command: str = "pytest -v",
        build_command: str | None = None,
        lint_command: str | None = None,
        project_path: str = ".",
    ):
        self._ai = ai_adapter
        self._test_cmd = test_command
        self._build_cmd = build_command
        self._lint_cmd = lint_command
        self._project_path = project_path

    def verify(self, ord_content: str) -> PipelineResult:
        """Run deterministic checks then AI assessment.

        Args:
            ord_content: The frozen ORD artifact content for context
        """
        # Step 1: Run tool checks
        tool_results = []

        # Tests
        test_result = self._run_command(self._test_cmd, "test_suite")
        tool_results.append(test_result)

        # Build (optional)
        if self._build_cmd:
            build_result = self._run_command(self._build_cmd, "build")
            tool_results.append(build_result)

        # Lint (optional)
        if self._lint_cmd:
            lint_result = self._run_command(self._lint_cmd, "lint")
            tool_results.append(lint_result)

        # Combine tool output
        tool_output = "\n\n".join(
            f"### {r['check']}\nExit code: {r['exit_code']}\n```\n{r['stdout'][-2000:]}\n```"
            for r in tool_results
        )

        all_passed = all(r["exit_code"] == 0 for r in tool_results)

        # Step 2: AI assessment
        request = AgentRequest(
            artifact_type="ORD-verification",
            event=LifecycleEvent.POST_VALIDATION,
            spec_content=ord_content,
            template_content="",
            prompt_content=(
                "You are assessing operational readiness. Below are the results "
                "of automated verification checks (test suite, build, lint). "
                "Assess whether the system meets operational readiness criteria "
                "based on these results and the ORD spec.\n\n"
                "Produce a JSON response with: status (pass/fail/warning), "
                "summary (one sentence), checks (per-check pass/fail), "
                "and recommendations (list of strings)."
            ),
            upstream_artifacts={"tool_results": tool_output},
            current_artifact=None,
            correction_constraints=[],
            metadata={"pipeline": "ord_verification"},
        )

        ai_response = self._ai.invoke(request)

        return PipelineResult(
            tool_output=tool_output,
            ai_assessment=ai_response.content,
            status="pass" if all_passed else "fail",
            details={
                "checks": tool_results,
                "all_tool_checks_passed": all_passed,
            },
        )

    def _run_command(self, command: str, check_name: str) -> dict:
        """Run a shell command and capture output."""
        import subprocess

        try:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=300,
                cwd=self._project_path,
            )
            return {
                "check": check_name,
                "command": command,
                "exit_code": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
            }
        except subprocess.TimeoutExpired:
            return {
                "check": check_name,
                "command": command,
                "exit_code": -1,
                "stdout": "",
                "stderr": "Command timed out after 300 seconds",
            }
        except Exception as e:
            return {
                "check": check_name,
                "command": command,
                "exit_code": -1,
                "stdout": "",
                "stderr": str(e),
            }


class CodebaseAnalysisPipeline:
    """Brownfield codebase analysis mixing SAST/linter output with AI interpretation.

    Step 1 (Tools): Run analysis tools:
      - SAST scanner (security findings)
      - Linter (code quality)
      - Dependency checker (outdated/vulnerable deps)
      - Optional: test coverage report

    Step 2 (AI): Interpret findings for AIEOS artifact intake:
      - Produce ACF-ready architecture context
      - Produce DCF-ready design constraints
      - Produce SAD-ready component inventory
    """

    def __init__(
        self,
        ai_adapter: AgentAdapter,
        project_path: str = ".",
        sast_command: str | None = None,
        lint_command: str | None = None,
        deps_command: str | None = None,
        coverage_command: str | None = None,
    ):
        self._ai = ai_adapter
        self._project_path = project_path
        self._sast_cmd = sast_command
        self._lint_cmd = lint_command
        self._deps_cmd = deps_command
        self._coverage_cmd = coverage_command

    def analyze(self, initiative_description: str = "") -> PipelineResult:
        """Run analysis tools then AI interpretation."""
        # Step 1: Run all configured tools
        tool_results = []

        if self._sast_cmd:
            tool_results.append(self._run_command(self._sast_cmd, "sast"))
        if self._lint_cmd:
            tool_results.append(self._run_command(self._lint_cmd, "lint"))
        if self._deps_cmd:
            tool_results.append(self._run_command(self._deps_cmd, "dependencies"))
        if self._coverage_cmd:
            tool_results.append(self._run_command(self._coverage_cmd, "coverage"))

        # Also gather file structure
        import subprocess

        try:
            tree = subprocess.run(
                "find . -name '*.py' -o -name '*.ts' -o -name '*.js' -o -name '*.go' | head -100",
                shell=True,
                capture_output=True,
                text=True,
                cwd=self._project_path,
                timeout=30,
            )
            tool_results.append(
                {
                    "check": "file_structure",
                    "command": "find",
                    "exit_code": 0,
                    "stdout": tree.stdout,
                    "stderr": "",
                }
            )
        except Exception:
            pass

        tool_output = "\n\n".join(
            f"### {r['check']}\n```\n{r['stdout'][-3000:]}\n```"
            for r in tool_results
        )

        # Step 2: AI interpretation for AIEOS intake
        request = AgentRequest(
            artifact_type="codebase-analysis",
            event=LifecycleEvent.PRE_GENERATION,
            spec_content="",
            template_content="",
            prompt_content=(
                "You are analyzing an existing codebase for AIEOS governance onboarding. "
                "Below are the results of automated analysis tools. "
                "Produce a structured analysis covering:\n\n"
                "1. **Architecture Context (ACF input):** Technology stack, runtime, "
                "frameworks, dependencies, deployment model\n"
                "2. **Design Context (DCF input):** Code quality findings, testing patterns, "
                "linting standards, security posture\n"
                "3. **Component Inventory (SAD input):** Major components identified from "
                "file structure, their responsibilities, and interactions\n"
                "4. **Risks and Concerns:** Security findings, outdated dependencies, "
                "code quality issues that should be addressed\n\n"
                f"Initiative context: {initiative_description}\n"
            ),
            upstream_artifacts={"tool_results": tool_output},
            current_artifact=None,
            correction_constraints=[],
            metadata={"pipeline": "codebase_analysis"},
        )

        ai_response = self._ai.invoke(request)

        return PipelineResult(
            tool_output=tool_output,
            ai_assessment=ai_response.content,
            status="complete",
            details={"tools_run": len(tool_results)},
        )

    def _run_command(self, command: str, check_name: str) -> dict:
        """Run a shell command and capture output."""
        import subprocess

        try:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=300,
                cwd=self._project_path,
            )
            return {
                "check": check_name,
                "command": command,
                "exit_code": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
            }
        except Exception as e:
            return {
                "check": check_name,
                "command": command,
                "exit_code": -1,
                "stdout": "",
                "stderr": str(e),
            }


class HealthReviewPipeline:
    """RRK health review mixing metrics collection with AI trend analysis.

    Step 1 (Tools): Collect operational metrics:
      - Read JSONL observability logs (harness metrics)
      - Run health check commands (API endpoints, DB connectivity)
      - Optionally query monitoring APIs (Datadog, Grafana)

    Step 2 (AI): Interpret trends and produce health assessment:
      - Analyze metric trends (latency, error rate, cost)
      - Compare against SRP SLO baselines
      - Produce structured RHR (Reliability Health Review) input
    """

    def __init__(
        self,
        ai_adapter: AgentAdapter,
        metrics_path: str | None = None,
        health_commands: dict[str, str] | None = None,
        slo_baselines: dict[str, float] | None = None,
    ):
        self._ai = ai_adapter
        self._metrics_path = metrics_path
        self._health_cmds = health_commands or {}
        self._slo_baselines = slo_baselines or {}

    def review(
        self, srp_content: str = "", prior_rhr_content: str = ""
    ) -> PipelineResult:
        """Collect metrics then AI assessment."""
        # Step 1: Collect metrics
        tool_results = []

        # Read observability logs
        if self._metrics_path:
            metrics = self._read_metrics(self._metrics_path)
            tool_results.append(
                {
                    "check": "observability_metrics",
                    "data": metrics,
                }
            )

        # Run health checks
        for name, cmd in self._health_cmds.items():
            result = self._run_command(cmd, f"health_{name}")
            tool_results.append(result)

        # Format tool output
        tool_parts = []
        for r in tool_results:
            if "data" in r:
                tool_parts.append(
                    f"### {r['check']}\n```json\n{r['data']}\n```"
                )
            else:
                tool_parts.append(
                    f"### {r['check']}\nExit code: {r.get('exit_code', 'N/A')}\n"
                    f"```\n{r.get('stdout', '')[-2000:]}\n```"
                )
        tool_output = "\n\n".join(tool_parts)

        # SLO baselines context
        slo_context = ""
        if self._slo_baselines:
            slo_lines = [f"- {k}: {v}" for k, v in self._slo_baselines.items()]
            slo_context = "\n\nSLO Baselines:\n" + "\n".join(slo_lines)

        # Step 2: AI trend analysis
        request = AgentRequest(
            artifact_type="health-review",
            event=LifecycleEvent.POST_VALIDATION,
            spec_content=srp_content,
            template_content="",
            prompt_content=(
                "You are conducting a Reliability Health Review (RHR). "
                "Below are operational metrics and health check results. "
                "Assess system health against SLO baselines.\n\n"
                "Produce a structured assessment covering:\n"
                "1. **SLO Compliance:** Each SLO metric vs baseline (met/breached)\n"
                "2. **Trends:** Is performance improving, stable, or degrading?\n"
                "3. **Incidents:** Any health check failures or anomalies?\n"
                "4. **Recommendations:** Prioritized actions for next review period\n"
                "5. **Overall Status:** healthy / degraded / critical\n"
                f"{slo_context}\n"
            ),
            upstream_artifacts={
                "metrics": tool_output,
                "prior_rhr": prior_rhr_content,
            },
            current_artifact=None,
            correction_constraints=[],
            metadata={"pipeline": "health_review"},
        )

        ai_response = self._ai.invoke(request)

        return PipelineResult(
            tool_output=tool_output,
            ai_assessment=ai_response.content,
            status="complete",
            details={
                "metrics_collected": len(tool_results),
                "slo_baselines": self._slo_baselines,
            },
        )

    def _read_metrics(self, path: str) -> str:
        """Read JSONL metrics file and produce summary."""
        import json

        metrics_path = Path(path)
        if not metrics_path.exists():
            return '{"error": "metrics file not found"}'

        lines = metrics_path.read_text(encoding="utf-8").strip().split("\n")
        # Take last 100 entries for recency
        recent = lines[-100:] if len(lines) > 100 else lines

        # Parse and summarize
        records = []
        for line in recent:
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue

        if not records:
            return '{"entries": 0}'

        # Basic aggregation
        total_cost = sum(r.get("cost_usd", 0) for r in records)
        avg_latency = sum(r.get("latency_ms", 0) for r in records) / len(records)
        failures = sum(1 for r in records if r.get("result") == "failure")

        summary = {
            "entries": len(records),
            "total_cost_usd": round(total_cost, 4),
            "avg_latency_ms": round(avg_latency, 1),
            "failure_count": failures,
            "failure_rate": round(failures / len(records), 3) if records else 0,
            "time_range": {
                "earliest": records[0].get("timestamp", ""),
                "latest": records[-1].get("timestamp", ""),
            },
        }

        return json.dumps(summary, indent=2)

    def _run_command(self, command: str, check_name: str) -> dict:
        import subprocess

        try:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=60,
            )
            return {
                "check": check_name,
                "command": command,
                "exit_code": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
            }
        except Exception as e:
            return {
                "check": check_name,
                "command": command,
                "exit_code": -1,
                "stdout": "",
                "stderr": str(e),
            }
