"""Tests for configuration loading."""

import os
from pathlib import Path

import pytest

from src.config import HarnessConfig, ProviderConfig, RoutingConfig, load_config


class TestProviderWorkspaceId:
    def test_workspace_id_parsed_from_yaml(self, tmp_path):
        cfg_file = tmp_path / "harness.yaml"
        cfg_file.write_text(
            "providers:\n"
            "  anthropic:\n"
            "    enabled: true\n"
            "    model: claude-haiku-4-5-20251001\n"
            "    workspace_id: wrk-abc123\n",
            encoding="utf-8",
        )
        config = load_config(cfg_file)
        assert config.providers["anthropic"].workspace_id == "wrk-abc123"

    def test_workspace_id_defaults_empty(self, tmp_path):
        cfg_file = tmp_path / "harness.yaml"
        cfg_file.write_text(
            "providers:\n  anthropic:\n    enabled: true\n", encoding="utf-8"
        )
        config = load_config(cfg_file)
        assert config.providers["anthropic"].workspace_id == ""


class TestLoadConfigFromYAML:
    def test_full_yaml(self, tmp_path):
        cfg_file = tmp_path / "harness.yaml"
        cfg_file.write_text(
            "aieos_root: /opt/aieos\n"
            "initiative_root: /opt/project\n"
            "max_convergence_iterations: 5\n"
            "observability_log: metrics.jsonl\n"
            "providers:\n"
            "  anthropic:\n"
            "    enabled: true\n"
            "    model: claude-3-opus\n"
            "    max_tokens: 16384\n"
            "  openai:\n"
            "    enabled: false\n"
            "    model: gpt-4\n"
            "routing:\n"
            "  default_strategy: pipeline\n"
            "  consensus_threshold: 0.8\n"
            "  cost_tiers:\n"
            "    - name: cheap\n"
            "      max_usd: 0.01\n"
            "bindings:\n"
            "  - tool: jira\n"
            "    adapter: jira-cloud\n"
        )
        config = load_config(cfg_file)
        assert config.aieos_root == "/opt/aieos"
        assert config.initiative_root == "/opt/project"
        assert config.max_convergence_iterations == 5
        assert config.observability_log == "metrics.jsonl"
        assert config.providers["anthropic"].enabled is True
        assert config.providers["anthropic"].model == "claude-3-opus"
        assert config.providers["anthropic"].max_tokens == 16384
        assert config.providers["openai"].enabled is False
        assert config.routing.default_strategy == "pipeline"
        assert config.routing.consensus_threshold == 0.8
        assert len(config.routing.cost_tiers) == 1
        assert len(config.bindings) == 1

    def test_missing_file_returns_defaults(self, tmp_path):
        config = load_config(tmp_path / "nonexistent.yaml")
        assert config.aieos_root == "../"
        assert config.initiative_root == ""
        assert config.providers == {}
        assert config.routing.default_strategy == "fallback"
        assert config.max_convergence_iterations == 3

    def test_empty_yaml(self, tmp_path):
        cfg_file = tmp_path / "empty.yaml"
        cfg_file.write_text("")
        config = load_config(cfg_file)
        assert config.aieos_root == "../"
        assert config.providers == {}


class TestEnvVarOverrides:
    def test_aieos_root_override(self, tmp_path, monkeypatch):
        cfg_file = tmp_path / "harness.yaml"
        cfg_file.write_text("aieos_root: /from/yaml\n")
        monkeypatch.setenv("AIEOS_ROOT", "/from/env")
        config = load_config(cfg_file)
        assert config.aieos_root == "/from/env"

    def test_initiative_root_override(self, tmp_path, monkeypatch):
        cfg_file = tmp_path / "harness.yaml"
        cfg_file.write_text("initiative_root: /from/yaml\n")
        monkeypatch.setenv("AIEOS_INITIATIVE_ROOT", "/from/env")
        config = load_config(cfg_file)
        assert config.initiative_root == "/from/env"

    def test_no_env_uses_yaml(self, tmp_path, monkeypatch):
        cfg_file = tmp_path / "harness.yaml"
        cfg_file.write_text("aieos_root: /from/yaml\n")
        monkeypatch.delenv("AIEOS_ROOT", raising=False)
        monkeypatch.delenv("AIEOS_INITIATIVE_ROOT", raising=False)
        config = load_config(cfg_file)
        assert config.aieos_root == "/from/yaml"


class TestProviderConfigDefaults:
    def test_defaults(self):
        pc = ProviderConfig()
        assert pc.enabled is False
        assert pc.model == ""
        assert pc.max_tokens == 8192

    def test_partial_yaml(self, tmp_path):
        cfg_file = tmp_path / "harness.yaml"
        cfg_file.write_text(
            "providers:\n"
            "  anthropic:\n"
            "    enabled: true\n"
        )
        config = load_config(cfg_file)
        assert config.providers["anthropic"].enabled is True
        assert config.providers["anthropic"].model == ""
        assert config.providers["anthropic"].max_tokens == 8192
