"""Tests for the CLI argument parsing and command dispatch."""

import pytest

from src.cli import main


class TestParseGenerate:
    """Parse 'generate' command correctly."""

    def test_generate_requires_type_and_initiative(self):
        """generate --type SAD --initiative ./proj parses correctly."""
        # main() calls sys.exit-style return; we test via argparse directly
        import argparse
        from src.cli import main as cli_main

        # Missing --type should fail
        with pytest.raises(SystemExit) as exc_info:
            cli_main(["generate", "--initiative", "./proj"])
        assert exc_info.value.code != 0

    def test_generate_missing_initiative(self):
        with pytest.raises(SystemExit) as exc_info:
            main(["generate", "--type", "SAD"])
        assert exc_info.value.code != 0

    def test_generate_parses_correctly(self):
        """Verify argparse accepts valid generate args (will fail at runtime
        because no config exists, but parsing succeeds)."""
        import argparse

        parser = argparse.ArgumentParser()
        parser.add_argument("--config", default="harness.yaml")
        sub = parser.add_subparsers(dest="command", required=True)
        gen = sub.add_parser("generate")
        gen.add_argument("--type", required=True)
        gen.add_argument("--initiative", required=True)

        args = parser.parse_args(
            ["generate", "--type", "SAD", "--initiative", "./proj"]
        )
        assert args.command == "generate"
        assert args.type == "SAD"
        assert args.initiative == "./proj"


class TestParseHealth:
    """Parse 'health' command correctly."""

    def test_health_no_extra_args(self):
        import argparse

        parser = argparse.ArgumentParser()
        parser.add_argument("--config", default="harness.yaml")
        sub = parser.add_subparsers(dest="command", required=True)
        sub.add_parser("health")

        args = parser.parse_args(["health"])
        assert args.command == "health"

    def test_health_with_config(self):
        import argparse

        parser = argparse.ArgumentParser()
        parser.add_argument("--config", default="harness.yaml")
        sub = parser.add_subparsers(dest="command", required=True)
        sub.add_parser("health")

        args = parser.parse_args(["--config", "custom.yaml", "health"])
        assert args.command == "health"
        assert args.config == "custom.yaml"


class TestParseCosts:
    """Parse 'costs' command."""

    def test_costs_with_initiative(self):
        import argparse

        parser = argparse.ArgumentParser()
        parser.add_argument("--config", default="harness.yaml")
        sub = parser.add_subparsers(dest="command", required=True)
        costs = sub.add_parser("costs")
        costs.add_argument("--initiative")

        args = parser.parse_args(["costs", "--initiative", "ALPHA"])
        assert args.command == "costs"
        assert args.initiative == "ALPHA"

    def test_costs_without_initiative(self):
        import argparse

        parser = argparse.ArgumentParser()
        parser.add_argument("--config", default="harness.yaml")
        sub = parser.add_subparsers(dest="command", required=True)
        costs = sub.add_parser("costs")
        costs.add_argument("--initiative")

        args = parser.parse_args(["costs"])
        assert args.command == "costs"
        assert args.initiative is None


class TestMissingArgs:
    """Missing required args produce error."""

    def test_no_command(self):
        with pytest.raises(SystemExit) as exc_info:
            main([])
        assert exc_info.value.code != 0

    def test_validate_missing_artifact(self):
        with pytest.raises(SystemExit) as exc_info:
            main(["validate"])
        assert exc_info.value.code != 0

    def test_lifecycle_missing_type(self):
        with pytest.raises(SystemExit) as exc_info:
            main(["lifecycle", "--initiative", "./proj"])
        assert exc_info.value.code != 0

    def test_lifecycle_missing_initiative(self):
        with pytest.raises(SystemExit) as exc_info:
            main(["lifecycle", "--type", "SAD"])
        assert exc_info.value.code != 0


class TestUnknownCommand:
    """Unknown command produces error."""

    def test_unknown_command(self):
        with pytest.raises(SystemExit) as exc_info:
            main(["nonexistent"])
        assert exc_info.value.code != 0


class TestParseValidate:
    """Parse 'validate' command."""

    def test_validate_parses(self):
        import argparse

        parser = argparse.ArgumentParser()
        parser.add_argument("--config", default="harness.yaml")
        sub = parser.add_subparsers(dest="command", required=True)
        val = sub.add_parser("validate")
        val.add_argument("--artifact", required=True)

        args = parser.parse_args(["validate", "--artifact", "docs/sdlc/03-sad.md"])
        assert args.command == "validate"
        assert args.artifact == "docs/sdlc/03-sad.md"


class TestParseLifecycle:
    """Parse 'lifecycle' command."""

    def test_lifecycle_parses(self):
        import argparse

        parser = argparse.ArgumentParser()
        parser.add_argument("--config", default="harness.yaml")
        sub = parser.add_subparsers(dest="command", required=True)
        lc = sub.add_parser("lifecycle")
        lc.add_argument("--type", required=True)
        lc.add_argument("--initiative", required=True)

        args = parser.parse_args(
            ["lifecycle", "--type", "TDD", "--initiative", "./my-project"]
        )
        assert args.command == "lifecycle"
        assert args.type == "TDD"
        assert args.initiative == "./my-project"


import json  # noqa: E402
from pathlib import Path  # noqa: E402

from src.freeze import hash_artifact_content  # noqa: E402
from src.models import ArtifactStatus  # noqa: E402


class TestParseFreeze:
    def test_freeze_requires_initiative_and_decision(self):
        with pytest.raises(SystemExit) as exc:
            main(["freeze", "--artifact", "SAD-TEST-001"])
        assert exc.value.code != 0

    def test_freeze_parses(self):
        import argparse

        parser = argparse.ArgumentParser()
        parser.add_argument("--config", default="harness.yaml")
        sub = parser.add_subparsers(dest="command", required=True)
        fr = sub.add_parser("freeze")
        fr.add_argument("--initiative", required=True)
        fr.add_argument("--artifact")
        fr.add_argument("--decision", required=True)
        fr.add_argument("--decided-by")
        args = parser.parse_args(
            ["freeze", "--initiative", "./p", "--decision", "d.json", "--decided-by", "Todd"]
        )
        assert args.command == "freeze"
        assert args.decided_by == "Todd"


def _freeze_initiative(tmp_path):
    sdlc = tmp_path / "docs" / "sdlc"
    sdlc.mkdir(parents=True)
    (sdlc / "05-sad.md").write_text(
        "## Document Control\n\n| Artifact ID | SAD-TEST-001 |\n| Status | FREEZE_PENDING |\n"
    )
    return hash_artifact_content((sdlc / "05-sad.md").read_text())


class TestFreezeCommandFunctional:
    def test_freeze_success_exit_zero(self, tmp_path, capsys):
        h = _freeze_initiative(tmp_path)
        decision = tmp_path / "decision.json"
        decision.write_text(json.dumps({
            "artifact_id": "SAD-TEST-001",
            "outcome": "APPROVE",
            "content_hash": h,
            "decided_by": "Todd",
        }))
        rc = main(["freeze", "--initiative", str(tmp_path), "--decision", str(decision)])
        assert rc == 0
        out = json.loads(capsys.readouterr().out)
        # G-14: the canonical FR-018 vocabulary, derived from the FreezeResult --
        # not the hardcoded lowercase literal this used to emit.
        assert out["status"] == "FROZEN"
        assert out["status"] == ArtifactStatus.FROZEN.value
        assert out["artifact_id"] == "SAD-TEST-001"
        # D1: owner defaults to decided_by and is reported in the payload.
        assert out["owner"] == "Todd"

    def test_freeze_writes_owner_from_decision(self, tmp_path, capsys):
        h = _freeze_initiative(tmp_path)
        decision = tmp_path / "decision.json"
        decision.write_text(json.dumps({
            "artifact_id": "SAD-TEST-001",
            "outcome": "APPROVE",
            "content_hash": h,
            "decided_by": "Todd",
            "owner": "Platform Team",
        }))
        rc = main(["freeze", "--initiative", str(tmp_path), "--decision", str(decision)])
        assert rc == 0
        out = json.loads(capsys.readouterr().out)
        assert out["owner"] == "Platform Team"
        text = (tmp_path / "docs" / "sdlc" / "05-sad.md").read_text()
        assert "| Owner | Platform Team |" in text
        assert "| Frozen By | Todd |" in text

    def test_freeze_hash_mismatch_exit_one(self, tmp_path, capsys):
        _freeze_initiative(tmp_path)
        decision = tmp_path / "decision.json"
        decision.write_text(json.dumps({
            "artifact_id": "SAD-TEST-001",
            "outcome": "APPROVE",
            "content_hash": "bad" * 20,
            "decided_by": "Todd",
        }))
        rc = main(["freeze", "--initiative", str(tmp_path), "--decision", str(decision)])
        assert rc == 1
        err = json.loads(capsys.readouterr().err)
        assert err["error"] == "hash_mismatch"

    def test_freeze_unknown_outcome_exit_two(self, tmp_path, capsys):
        _freeze_initiative(tmp_path)
        decision = tmp_path / "decision.json"
        decision.write_text(json.dumps({
            "artifact_id": "SAD-TEST-001", "outcome": "MAYBE",
            "content_hash": "x", "decided_by": "Todd",
        }))
        rc = main(["freeze", "--initiative", str(tmp_path), "--decision", str(decision)])
        assert rc == 2


import json as _dfjson  # noqa: E402
from pathlib import Path as _DFPath  # noqa: E402


class TestParseSubprocessSeam:
    def test_run_artifact_requires_all_args(self):
        with pytest.raises(SystemExit):
            main(["run-artifact", "--type", "PRD"])  # missing --initiative/--aieos-root

    def test_read_state_requires_initiative(self):
        with pytest.raises(SystemExit):
            main(["read-state"])


class TestReadStateCommand:
    def test_emits_state_json(self, tmp_path, capsys):
        eng = tmp_path / "docs" / "engagement"
        eng.mkdir(parents=True)
        (eng / "er.md").write_text(
            "## 1b\n\n| Field | Value |\n|--|--|\n"
            "| Current Layer | Layer 4 |\n| Current Artifact | EEK:PRD |\n"
            "| Frozen Count | 3 |\n"
        )
        rc = main(["--config", "nope.yaml", "read-state", "--initiative", str(tmp_path)])
        assert rc == 0
        out = _dfjson.loads(capsys.readouterr().out)
        assert out["current_layer"] == "Layer 4"
        assert out["frozen_count"] == 3

    def test_missing_er_errors(self, tmp_path, capsys):
        rc = main(["--config", "nope.yaml", "read-state", "--initiative", str(tmp_path)])
        assert rc == 1
        assert _dfjson.loads(capsys.readouterr().err)["error"] == "no_state"


class TestRunArtifactCommand:
    def test_no_providers_errors(self, tmp_path, capsys):
        rc = main([
            "--config", "nope.yaml", "run-artifact",
            "--type", "PRD", "--initiative", str(tmp_path), "--aieos-root", str(tmp_path),
        ])
        assert rc == 1
        assert _dfjson.loads(capsys.readouterr().err)["error"] == "no_providers"


class TestRunArtifactHonoursConfiguredBudget:
    """G-17: max_convergence_iterations must actually reach the driver.

    It was parsed into HarnessConfig and never passed to HarnessDriver, so the
    driver's default of 3 always won and the yaml knob did nothing on the one
    path the dark factory uses. Setting it to 2 to cap spend during the
    2026-07-14 dogfood was silently ignored.

    Asserts the value the driver is CONSTRUCTED with -- the bug was in the
    hand-off, not in the loop, and the loop already had tests.
    """

    def _config(self, tmp_path, body: str) -> str:
        cfg = tmp_path / "harness.yaml"
        cfg.write_text(body, encoding="utf-8")
        return str(cfg)

    def _spy_driver(self, monkeypatch, captured):
        import src.driver as driver_mod
        from src.models import LifecycleResult

        class SpyDriver(driver_mod.HarnessDriver):
            def __init__(self, *a, **kw):
                captured.update(kw)
                super().__init__(*a, **kw)

            def run_artifact(self, artifact_type):  # don't need a real run
                return LifecycleResult.CONVERGED

        # cli.py imports HarnessDriver lazily inside the command, so patching
        # the module attribute is what the call site will resolve.
        monkeypatch.setattr(driver_mod, "HarnessDriver", SpyDriver)

    def test_configured_budget_reaches_the_driver(self, tmp_path, monkeypatch):
        captured: dict = {}
        self._spy_driver(monkeypatch, captured)
        cfg = self._config(
            tmp_path,
            "providers:\n  mock:\n    enabled: true\n"
            "max_convergence_iterations: 2\n",
        )
        rc = main([
            "--config", cfg, "run-artifact", "--type", "PRD",
            "--initiative", str(tmp_path), "--aieos-root", str(tmp_path),
        ])
        assert rc == 0
        assert captured["max_iterations"] == 2

    def test_default_budget_is_used_when_unset(self, tmp_path, monkeypatch):
        captured: dict = {}
        self._spy_driver(monkeypatch, captured)
        cfg = self._config(tmp_path, "providers:\n  mock:\n    enabled: true\n")
        rc = main([
            "--config", cfg, "run-artifact", "--type", "PRD",
            "--initiative", str(tmp_path), "--aieos-root", str(tmp_path),
        ])
        assert rc == 0
        assert captured["max_iterations"] == 3  # HarnessConfig default


class TestMarkStatusCommand:
    def _artifact(self, tmp_path, status="FREEZE_PENDING"):
        d = tmp_path / "docs" / "sdlc"
        d.mkdir(parents=True)
        (d / "05-sad.md").write_text(
            "## Document Control\n\n| Artifact ID | SAD-X-001 |\n"
            f"| Status | {status} |\n"
        )

    def test_marks_faulted(self, tmp_path, capsys):
        self._artifact(tmp_path)
        rc = main(["--config", "n.yaml", "mark-status", "--initiative", str(tmp_path),
                   "--artifact", "SAD-X-001", "--status", "FAULTED"])
        assert rc == 0
        assert _dfjson.loads(capsys.readouterr().out)["status"] == "FAULTED"
        from src.state import read_frozen_artifacts
        from src.models import ArtifactStatus
        assert read_frozen_artifacts(tmp_path)["SAD-X-001"] == ArtifactStatus.FAULTED

    def test_marks_halted(self, tmp_path, capsys):
        self._artifact(tmp_path)
        rc = main(["--config", "n.yaml", "mark-status", "--initiative", str(tmp_path),
                   "--artifact", "SAD-X-001", "--status", "HALTED"])
        assert rc == 0

    def test_refuses_frozen(self, tmp_path, capsys):
        self._artifact(tmp_path)
        rc = main(["--config", "n.yaml", "mark-status", "--initiative", str(tmp_path),
                   "--artifact", "SAD-X-001", "--status", "FROZEN"])
        assert rc == 2
        assert _dfjson.loads(capsys.readouterr().err)["error"] == "bad_status"

    def test_unknown_artifact_errors(self, tmp_path, capsys):
        self._artifact(tmp_path)
        rc = main(["--config", "n.yaml", "mark-status", "--initiative", str(tmp_path),
                   "--artifact", "NOPE-001", "--status", "HALTED"])
        assert rc == 1
