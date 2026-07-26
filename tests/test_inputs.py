"""Tests for declared-inputs resolution (G-3/G-5, manifest 1.1)."""

from __future__ import annotations

import pytest

from src.inputs import resolve_declared_inputs

MANIFEST = """\
manifest_version: "1.1"
kits:
  EEK:
    layer: 4
    full_name: "Engineering Execution Kit"
    repository: "aieos-engineering-execution"
    category: "pipeline"
    status: "built"
    artifacts:
      - id: PRD
        full_name: "Product Requirements Document"
        spec_file: "prd-spec.md"
        human_authored: false
        inputs:
          - { ref: "docs/brief.md", role: "brief", source: "human" }
          - { ref: "docs/principles/product-craftsmanship.md", role: "principles", source: "framework" }
      - id: SAD
        full_name: "Solution Architecture Document"
        spec_file: "sad-spec.md"
        human_authored: false
    artifact_flow: [PRD, SAD]
dependency_edges: []
"""


@pytest.fixture()
def aieos_root(tmp_path):
    root = tmp_path / "aieos"
    gf = root / "aieos-governance-foundation"
    gf.mkdir(parents=True)
    (gf / "kit-manifest.yml").write_text(MANIFEST, encoding="utf-8")
    kit = root / "aieos-engineering-execution" / "docs" / "principles"
    kit.mkdir(parents=True)
    (kit / "product-craftsmanship.md").write_text(
        "# Product Craftsmanship\nOutcomes over output.\n", encoding="utf-8"
    )
    return root


@pytest.fixture()
def initiative(tmp_path):
    init = tmp_path / "init"
    init.mkdir()
    return init


def test_framework_input_resolved(aieos_root, initiative):
    resolved = resolve_declared_inputs(aieos_root, initiative, "PRD")
    key = "principles: docs/principles/product-craftsmanship.md"
    assert key in resolved
    assert "Outcomes over output" in resolved[key]


def test_missing_framework_input_fails_fast(aieos_root, initiative):
    (
        aieos_root
        / "aieos-engineering-execution"
        / "docs"
        / "principles"
        / "product-craftsmanship.md"
    ).unlink()
    with pytest.raises(ValueError, match="product-craftsmanship"):
        resolve_declared_inputs(aieos_root, initiative, "PRD")


def test_human_input_optional_when_absent(aieos_root, initiative):
    resolved = resolve_declared_inputs(aieos_root, initiative, "PRD")
    assert not any(k.startswith("brief") for k in resolved)


def test_human_input_loaded_when_present(aieos_root, initiative):
    docs = initiative / "docs"
    docs.mkdir()
    (docs / "brief.md").write_text("# Brief\nBuild the thing.\n", encoding="utf-8")
    resolved = resolve_declared_inputs(aieos_root, initiative, "PRD")
    assert "Build the thing" in resolved["brief: docs/brief.md"]


def test_artifact_without_inputs_resolves_empty(aieos_root, initiative):
    assert resolve_declared_inputs(aieos_root, initiative, "SAD") == {}


def test_no_manifest_resolves_empty(tmp_path, initiative):
    assert resolve_declared_inputs(tmp_path, initiative, "PRD") == {}


def test_run_artifact_passes_declared_inputs_to_adapter(aieos_root, initiative):
    """End to end: the generator adapter receives the principles content."""
    import json

    from src.adapters.mock import MockAdapter
    from src.driver import HarnessDriver

    # run_artifact resolves kit files by directory scan; give the fake kit
    # the four files for PRD under the SAME repo the manifest names.
    kit = aieos_root / "aieos-engineering-execution"
    for sub, name, content in (
        ("specs", "prd-spec.md", "# PRD spec"),
        ("artifacts", "prd-template.md", "template"),
        ("prompts", "prd-prompt.md", "prompt"),
        ("validators", "prd-validator.md", "validate"),
    ):
        d = kit / "docs" / sub
        d.mkdir(parents=True, exist_ok=True)
        (d / name).write_text(content, encoding="utf-8")

    passing = json.dumps(
        {
            "status": "PASS",
            "summary": "ok",
            "hard_gates": {"g": "PASS"},
            "blocking_issues": [],
            "warnings": [],
            "completeness_score": 90,
        }
    )

    class CapturingAdapter(MockAdapter):
        def __init__(self, **kw):
            super().__init__(**kw)
            self.seen = []

        def invoke(self, request):
            self.seen.append(request)
            return super().invoke(request)

    gen = CapturingAdapter()
    val = CapturingAdapter(preset_responses={"PRD": passing})
    driver = HarnessDriver(initiative, gen, val, aieos_root=aieos_root)
    driver.run_artifact("PRD")

    key = "principles: docs/principles/product-craftsmanship.md"
    assert gen.seen, "generator adapter never invoked"
    assert key in gen.seen[0].declared_inputs
    assert "Outcomes over output" in gen.seen[0].declared_inputs[key]
    assert key in val.seen[0].declared_inputs
