"""Tests for context builder — isolated context packages for work item phases."""

import pytest
from src.wdd_parser import ExecutionItem
from src.context_builder import build_context, ItemContext, PHASES


SAMPLE_WDD_CONTENT = """\
# WDD: Test Initiative

## 2. Work Items

### WDD Item
- WDD Item ID: WDD-TEST-001
- Assignee Type: AI Agent
- Complexity Estimate: M

#### Intent (1-2 sentences)
Implement data models.

#### Acceptance Criteria (Executable)
- AC1: Models importable.

#### Dependencies
None

---

### WDD Item
- WDD Item ID: WDD-TEST-002
- Assignee Type: AI Agent
- Complexity Estimate: S

#### Intent (1-2 sentences)
Implement configuration loader.

#### Acceptance Criteria (Executable)
- AC1: Config loads.

#### Dependencies
- WDD-TEST-001

---
"""

SAMPLE_TDD_CONTENT = """\
# TDD: Test Initiative

## 3. Technical Overview

The system uses data models defined in models.py.

## 4. Component Design

### 4.1 Data Models

The models module contains WDD-TEST-001 types.

### 4.2 Configuration

The config module loads YAML settings.
"""

SAMPLE_ACF_CONTENT = """\
# Architecture Conformance Framework

## Constraints
- No circular dependencies
- All modules must be importable independently
"""

SAMPLE_DCF_CONTENT = """\
# Design Conformance Framework

## Patterns
- Use dataclasses for data containers
- Use Protocol for interfaces
"""

SAMPLE_PROMPT = """\
You are generating tests for a work item.
Follow TDD practices. Write unit tests first.
"""


@pytest.fixture
def item_001():
    return ExecutionItem(
        id="WDD-TEST-001",
        title="Implement data models",
        description="Implement data models.",
        assignee_type="AI",
        acceptance_criteria=["AC1: Models importable."],
        complexity="M",
        dependencies=[],
        work_group="Foundation",
    )


@pytest.fixture
def item_002():
    return ExecutionItem(
        id="WDD-TEST-002",
        title="Implement configuration loader",
        description="Implement configuration loader.",
        assignee_type="AI",
        acceptance_criteria=["AC1: Config loads."],
        complexity="S",
        dependencies=["WDD-TEST-001"],
        work_group="Foundation",
        files_touched=["src/config.py"],
    )


def test_build_phase1_no_prior(item_001):
    """Phase 'tests' has no prior outputs."""
    ctx = build_context(
        item=item_001,
        wdd_content=SAMPLE_WDD_CONTENT,
        tdd_content=SAMPLE_TDD_CONTENT,
        acf_content=SAMPLE_ACF_CONTENT,
        dcf_content=SAMPLE_DCF_CONTENT,
        phase="tests",
        prompt_content=SAMPLE_PROMPT,
        prior_outputs=None,
    )
    assert isinstance(ctx, ItemContext)
    assert ctx.prior_phase_outputs == {}


def test_build_phase3_with_prior(item_001):
    """Phase 'code' includes tests + plan prior outputs."""
    prior = {
        "tests": "def test_models(): pass",
        "plan": "Step 1: Create models.py\nStep 2: Add dataclasses",
    }
    ctx = build_context(
        item=item_001,
        wdd_content=SAMPLE_WDD_CONTENT,
        tdd_content=SAMPLE_TDD_CONTENT,
        acf_content=SAMPLE_ACF_CONTENT,
        dcf_content=SAMPLE_DCF_CONTENT,
        phase="code",
        prompt_content="Generate code.",
        prior_outputs=prior,
    )
    assert "tests" in ctx.prior_phase_outputs
    assert "plan" in ctx.prior_phase_outputs
    assert "def test_models" in ctx.prior_phase_outputs["tests"]
    assert "Step 1" in ctx.prior_phase_outputs["plan"]


def test_wdd_item_extracted(item_001):
    """Context contains only this item's WDD section, not other items."""
    ctx = build_context(
        item=item_001,
        wdd_content=SAMPLE_WDD_CONTENT,
        tdd_content=SAMPLE_TDD_CONTENT,
        acf_content=SAMPLE_ACF_CONTENT,
        dcf_content=SAMPLE_DCF_CONTENT,
        phase="tests",
        prompt_content=SAMPLE_PROMPT,
    )
    assert "WDD-TEST-001" in ctx.wdd_item_text
    # Should not contain the other item's section as the primary match
    assert "WDD-TEST-002" not in ctx.wdd_item_text


def test_tdd_sections_extracted(item_001):
    """TDD content relevant to this item is present."""
    ctx = build_context(
        item=item_001,
        wdd_content=SAMPLE_WDD_CONTENT,
        tdd_content=SAMPLE_TDD_CONTENT,
        acf_content=SAMPLE_ACF_CONTENT,
        dcf_content=SAMPLE_DCF_CONTENT,
        phase="tests",
        prompt_content=SAMPLE_PROMPT,
    )
    assert ctx.tdd_sections != ""
    # Should include the section that references WDD-TEST-001
    assert "WDD-TEST-001" in ctx.tdd_sections or "models" in ctx.tdd_sections.lower()


def test_acf_dcf_included(item_001):
    """ACF and DCF content are fully present in context."""
    ctx = build_context(
        item=item_001,
        wdd_content=SAMPLE_WDD_CONTENT,
        tdd_content=SAMPLE_TDD_CONTENT,
        acf_content=SAMPLE_ACF_CONTENT,
        dcf_content=SAMPLE_DCF_CONTENT,
        phase="tests",
        prompt_content=SAMPLE_PROMPT,
    )
    assert ctx.acf_sections == SAMPLE_ACF_CONTENT
    assert ctx.dcf_sections == SAMPLE_DCF_CONTENT


def test_phase_prompt_included(item_001):
    """Phase prompt content is present in the context."""
    ctx = build_context(
        item=item_001,
        wdd_content=SAMPLE_WDD_CONTENT,
        tdd_content=SAMPLE_TDD_CONTENT,
        acf_content=SAMPLE_ACF_CONTENT,
        dcf_content=SAMPLE_DCF_CONTENT,
        phase="tests",
        prompt_content=SAMPLE_PROMPT,
    )
    assert ctx.phase_prompt == SAMPLE_PROMPT
    assert "TDD practices" in ctx.phase_prompt
