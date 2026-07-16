"""Tests for WDD parser — work item and group extraction from WDD Markdown."""

import pytest
from src.wdd_parser import (
    ExecutionItem,
    ExecutionGroup,
    ExecutionPlan,
    parse_wdd_for_execution,
)


SAMPLE_WDD = """\
# WDD: Test Initiative

## 0. Document Control
- WDD ID: WDD-TESTINIT-001
- Author: Test Author
- Date: 2026-03-27
- Status: Frozen

---

## 1. Scope and Non-Goals (Copied from TDD)

### In Scope
- Feature alpha
- Feature beta

---

## 2. Work Items

### WDD Item
- WDD Item ID: WDD-TESTINIT-001
- Parent TDD Section: §3 Overview
- Assignee Type: AI Agent
- Required Capabilities: backend
- Complexity Estimate: S — Simple data models

#### Intent (1-2 sentences)
Implement core data models for the test initiative.

#### In Scope
- 3 dataclasses

#### Out of Scope / Non-Goals
- No business logic

#### Inputs
- TDD §3

#### Outputs
- `src/models.py`

#### Acceptance Criteria (Executable)
- AC1: Given models are imported, When instantiated, Then fields are correct.
- AC2: Given models are imported, When defaults used, Then no error.

#### Definition of Done (Hard)
- [x] PR merged
- [x] Tests passing

#### Interface Contract References
- Provider: TDD §3

#### Dependencies
None

#### Rollback / Failure Behavior
Revert src/models.py.

---

### WDD Item
- WDD Item ID: WDD-TESTINIT-002
- Parent TDD Section: §4 Config
- Assignee Type: Human
- Required Capabilities: configuration
- Complexity Estimate: M — Configuration loading

#### Intent (1-2 sentences)
Implement configuration loading system.

#### In Scope
- YAML loading
- Env var overrides

#### Out of Scope / Non-Goals
- No hot-reload

#### Inputs
- config.yaml

#### Outputs
- `src/config.py`

#### Acceptance Criteria (Executable)
- AC1: Given valid YAML, When loaded, Then config populated.
- AC2: Given env var set, When loaded, Then env var overrides YAML.
- AC3: Given no file, When loaded, Then defaults returned.

#### Definition of Done (Hard)
- [x] PR merged
- [x] Tests passing

#### Interface Contract References
- Consumer: CLI

#### Dependencies
- WDD-TESTINIT-001 (uses models)

#### Rollback / Failure Behavior
Revert src/config.py.

---

### WDD Item
- WDD Item ID: WDD-TESTINIT-003
- Parent TDD Section: §5 State Manager
- Assignee Type: Either
- Required Capabilities: filesystem
- Complexity Estimate: L — Multiple parsing functions

#### Intent (1-2 sentences)
Implement state management for reading and writing governance state.

#### In Scope
- ER state block read/write
- Journal operations

#### Out of Scope / Non-Goals
- No database

#### Inputs
- ER Markdown files
- Journal files

#### Outputs
- `src/state.py`

#### Acceptance Criteria (Executable)
- AC1: Given ER file, When read, Then state block extracted.

#### Definition of Done (Hard)
- [x] PR merged
- [x] Tests passing

#### Interface Contract References
- Provider: Invariant Enforcer

#### Dependencies
- WDD-TESTINIT-001 (uses models)
- WDD-TESTINIT-002 (uses config)

#### Rollback / Failure Behavior
Revert src/state.py.

---

## 3. Work Groups

### Work Group
- Group ID: WG-01
- Group Name: Foundation
- Business Capability: Core data and configuration
- Member Items: WDD-TESTINIT-001, WDD-TESTINIT-002
- Acceptance Criteria (Group-Level): Models and config importable.

### Work Group
- Group ID: WG-02
- Group Name: State Layer
- Business Capability: Governance state read/write
- Member Items: WDD-TESTINIT-003
- Acceptance Criteria (Group-Level): State operations work correctly.

---

## 4. Freeze Declaration (when ready)
Frozen.
"""


SAMPLE_EXEC_PLAN = """\
# Execution Plan: TESTINIT

## Baseline
- Baseline Tests: 42
- Baseline Build: clean

## Item Overrides
- WDD-TESTINIT-003: parallel_safe: false
- WDD-TESTINIT-001 files: src/models.py, tests/test_models.py
- WDD-TESTINIT-002 files: src/config.py

## Group Modes
- Foundation Mode: phase_sync
- State Layer Mode: independent
"""


@pytest.fixture
def wdd_file(tmp_path):
    """Write sample WDD to a temp file and return its path.

    encoding="utf-8" is load-bearing (G-6). The sample contains "§" (U+00A7);
    without an explicit encoding this writes at the locale default, which on
    Windows is cp1252, so "§" lands as the single byte 0xa7. wdd_parser reads
    with an explicit encoding="utf-8", 0xa7 is not valid UTF-8, and every test
    in this module dies with UnicodeDecodeError. Artifacts are UTF-8 -- say so
    on both sides of every file operation.
    """
    p = tmp_path / "08-wdd.md"
    p.write_text(SAMPLE_WDD, encoding="utf-8")
    return str(p)


@pytest.fixture
def exec_plan_file(tmp_path):
    """Write sample execution plan to a temp file and return its path."""
    p = tmp_path / "13-execution-plan.md"
    p.write_text(SAMPLE_EXEC_PLAN, encoding="utf-8")
    return str(p)


def test_parse_metadata(wdd_file):
    """WDD ID and initiative are extracted from document control."""
    plan = parse_wdd_for_execution(wdd_file)
    assert plan.wdd_id == "WDD-TESTINIT-001"
    assert plan.initiative == "TESTINIT"


def test_parse_work_groups(wdd_file):
    """Two work groups are found with correct names."""
    plan = parse_wdd_for_execution(wdd_file)
    assert len(plan.groups) == 2
    names = [g.name for g in plan.groups]
    assert "Foundation" in names
    assert "State Layer" in names


def test_parse_work_items(wdd_file):
    """Three work items are found across all groups."""
    plan = parse_wdd_for_execution(wdd_file)
    all_items = [item for g in plan.groups for item in g.items]
    assert len(all_items) == 3
    ids = {item.id for item in all_items}
    assert ids == {
        "WDD-TESTINIT-001",
        "WDD-TESTINIT-002",
        "WDD-TESTINIT-003",
    }


def test_parse_assignee_types(wdd_file):
    """AI, Human, and Either assignee types are parsed correctly."""
    plan = parse_wdd_for_execution(wdd_file)
    items_by_id = {
        item.id: item
        for g in plan.groups
        for item in g.items
    }
    assert items_by_id["WDD-TESTINIT-001"].assignee_type == "AI"
    assert items_by_id["WDD-TESTINIT-002"].assignee_type == "Human"
    assert items_by_id["WDD-TESTINIT-003"].assignee_type == "Either"


def test_parse_acceptance_criteria(wdd_file):
    """Acceptance criteria counts are correct per item."""
    plan = parse_wdd_for_execution(wdd_file)
    items_by_id = {
        item.id: item
        for g in plan.groups
        for item in g.items
    }
    assert len(items_by_id["WDD-TESTINIT-001"].acceptance_criteria) == 2
    assert len(items_by_id["WDD-TESTINIT-002"].acceptance_criteria) == 3
    assert len(items_by_id["WDD-TESTINIT-003"].acceptance_criteria) == 1


def test_parse_dependencies(wdd_file):
    """Item 3 depends on items 1 and 2."""
    plan = parse_wdd_for_execution(wdd_file)
    items_by_id = {
        item.id: item
        for g in plan.groups
        for item in g.items
    }
    assert items_by_id["WDD-TESTINIT-001"].dependencies == []
    assert items_by_id["WDD-TESTINIT-002"].dependencies == [
        "WDD-TESTINIT-001"
    ]
    assert set(items_by_id["WDD-TESTINIT-003"].dependencies) == {
        "WDD-TESTINIT-001",
        "WDD-TESTINIT-002",
    }


def test_parse_complexity(wdd_file):
    """S, M, L complexity values are parsed correctly."""
    plan = parse_wdd_for_execution(wdd_file)
    items_by_id = {
        item.id: item
        for g in plan.groups
        for item in g.items
    }
    assert items_by_id["WDD-TESTINIT-001"].complexity == "S"
    assert items_by_id["WDD-TESTINIT-002"].complexity == "M"
    assert items_by_id["WDD-TESTINIT-003"].complexity == "L"


def test_default_parallel_safe(wdd_file):
    """All items default to parallel_safe=True without execution plan."""
    plan = parse_wdd_for_execution(wdd_file)
    all_items = [item for g in plan.groups for item in g.items]
    assert all(item.parallel_safe is True for item in all_items)
