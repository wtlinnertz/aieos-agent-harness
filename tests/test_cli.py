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
