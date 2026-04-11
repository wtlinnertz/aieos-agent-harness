"""Non-LLM tool adapter for the AIEOS Agent Harness.

Runs external tools (SAST scanners, linters, validators) as subprocesses.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from src.models import AgentRequest, AgentResponse, HealthStatus


class ToolAdapter:
    """Adapter for non-LLM tools (SAST scanners, linters, etc.)."""

    def __init__(
        self,
        name: str,
        command: str,
        args: list[str] | None = None,
        timeout: int = 300,
    ) -> None:
        self._name = name
        self._command = command
        self._args = args or []
        self._timeout = timeout

    @property
    def provider_name(self) -> str:
        return f"tool:{self._name}"

    @property
    def model_name(self) -> str:
        return self._command

    def invoke(self, request: AgentRequest) -> AgentResponse:
        """Run tool as subprocess.

        If request.current_artifact is set, write it to a temp file and
        pass the path as the last argument. Capture stdout as response
        content. Track execution time.
        """
        start = time.monotonic()
        cmd = [self._command] + list(self._args)
        tmp_file: Path | None = None

        try:
            # Write current artifact to temp file if present
            if request.current_artifact:
                tmp = tempfile.NamedTemporaryFile(
                    mode="w",
                    suffix=".md",
                    delete=False,
                    prefix=f"harness_{request.artifact_type}_",
                )
                tmp.write(request.current_artifact)
                tmp.close()
                tmp_file = Path(tmp.name)
                cmd.append(str(tmp_file))

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self._timeout,
            )

            latency_ms = (time.monotonic() - start) * 1000
            content = result.stdout
            if result.returncode != 0 and result.stderr:
                content += "\n\nSTDERR:\n" + result.stderr

            return AgentResponse(
                content=content,
                provider=self.provider_name,
                model=self._command,
                tokens_in=0,
                tokens_out=0,
                cost_usd=0.0,
                latency_ms=round(latency_ms, 1),
                raw_response={
                    "returncode": result.returncode,
                    "stderr": result.stderr,
                },
            )

        except subprocess.TimeoutExpired:
            latency_ms = (time.monotonic() - start) * 1000
            return AgentResponse(
                content=f"Tool '{self._name}' timed out after {self._timeout}s",
                provider=self.provider_name,
                model=self._command,
                tokens_in=0,
                tokens_out=0,
                cost_usd=0.0,
                latency_ms=round(latency_ms, 1),
                raw_response={"error": "timeout"},
            )

        except FileNotFoundError:
            latency_ms = (time.monotonic() - start) * 1000
            return AgentResponse(
                content=f"Tool '{self._name}' command not found: {self._command}",
                provider=self.provider_name,
                model=self._command,
                tokens_in=0,
                tokens_out=0,
                cost_usd=0.0,
                latency_ms=round(latency_ms, 1),
                raw_response={"error": "command_not_found"},
            )

        finally:
            if tmp_file is not None and tmp_file.exists():
                tmp_file.unlink()

    def health(self) -> HealthStatus:
        """Check if the command exists on PATH."""
        if shutil.which(self._command) is not None:
            return HealthStatus.OK
        return HealthStatus.DOWN

    def cost_estimate(self, request: AgentRequest) -> float:
        """Tools are free."""
        return 0.0
