"""Per-case upstream context for gold cases (gold-case.yaml `upstream_dir`).

A gold case written against another initiative's PRD must be judged with
that PRD. One shared upstream set handed the G-9 receipt case the wrong
PRD, and the R2 referential check exposed it (2026-11-16).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.calibration import CalibrationError, load_gold_set, run_calibration, write_runs
from src.cli import _collect_case_upstream
from tests.test_calibration import ScriptedJudgeAdapter, build_gold_set

PROMPT = "validator prompt"


def _write_upstream(directory: Path, artifact_id: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{artifact_id.lower()}.md").write_text(
        f"## Document Control\n\n| Artifact ID | {artifact_id} |\n", encoding="utf-8"
    )


def _gold_with_override(tmp_path: Path) -> tuple[Path, str]:
    gold = tmp_path / "gold"
    ids = build_gold_set(gold)
    target = ids[0]
    case_file = gold / f"{target}.yaml"
    case_file.write_text(
        case_file.read_text(encoding="utf-8") + "upstream_dir: upstream-other\n",
        encoding="utf-8",
    )
    _write_upstream(gold / "upstream-other", "PRD-OTHER-001")
    return gold, target


class TestGoldCaseUpstreamDir:
    def test_load_gold_set_upstream_dir_resolves_relative_to_case_file(self, tmp_path):
        gold, target = _gold_with_override(tmp_path)

        cases = {c.case_id: c for c in load_gold_set(gold)}

        assert cases[target].upstream_dir == (gold / "upstream-other").resolve()

    def test_load_gold_set_without_upstream_dir_is_none(self, tmp_path):
        gold, target = _gold_with_override(tmp_path)

        cases = [c for c in load_gold_set(gold) if c.case_id != target]

        assert all(c.upstream_dir is None for c in cases)


class TestCollectCaseUpstream:
    def test_collect_case_upstream_reads_only_overriding_cases(self, tmp_path):
        gold, target = _gold_with_override(tmp_path)
        cases = load_gold_set(gold)

        per_case = _collect_case_upstream(cases)

        assert list(per_case) == [target]
        assert list(per_case[target]) == ["PRD-OTHER-001"]

    def test_collect_case_upstream_missing_dir_refuses(self, tmp_path):
        gold, _ = _gold_with_override(tmp_path)
        for f in (gold / "upstream-other").iterdir():
            f.unlink()
        (gold / "upstream-other").rmdir()
        cases = load_gold_set(gold)

        with pytest.raises(CalibrationError) as exc:
            _collect_case_upstream(cases)

        assert exc.value.code == "missing_upstream"


class TestRunCalibrationCaseUpstream:
    def test_case_upstream_overrides_default_for_that_case_only(self, tmp_path):
        gold, target = _gold_with_override(tmp_path)
        cases = load_gold_set(gold)
        judge = ScriptedJudgeAdapter()

        run_calibration(
            cases,
            judge,
            validator_prompt=PROMPT,
            upstream_artifacts={"PRD-SHARED-001": "shared"},
            case_upstream={target: {"PRD-OTHER-001": "other"}},
        )

        seen = {
            r.metadata["case_id"]: sorted(r.upstream_artifacts) for r in judge.calls
        }
        assert seen[target] == ["PRD-OTHER-001"]
        assert {v[0] for k, v in seen.items() if k != target} == {"PRD-SHARED-001"}

    def test_upstream_ids_recorded_per_case_and_as_union(self, tmp_path):
        gold, target = _gold_with_override(tmp_path)
        cases = load_gold_set(gold)

        runs = run_calibration(
            cases,
            ScriptedJudgeAdapter(),
            validator_prompt=PROMPT,
            upstream_artifacts={"PRD-SHARED-001": "shared"},
            case_upstream={target: {"PRD-OTHER-001": "other"}},
        )

        per_case = {cr.case.case_id: cr.upstream_artifact_ids for cr in runs.case_runs}
        assert per_case[target] == ["PRD-OTHER-001"]
        assert runs.upstream_artifact_ids == ["PRD-OTHER-001", "PRD-SHARED-001"]

    def test_runs_file_carries_per_case_upstream_ids(self, tmp_path):
        gold, target = _gold_with_override(tmp_path)
        runs = run_calibration(
            load_gold_set(gold),
            ScriptedJudgeAdapter(),
            validator_prompt=PROMPT,
            upstream_artifacts={"PRD-SHARED-001": "shared"},
            case_upstream={target: {"PRD-OTHER-001": "other"}},
        )

        path = write_runs(runs, tmp_path / "runs.json")

        cases = {c["case_id"]: c for c in json.loads(path.read_text())["cases"]}
        assert cases[target]["upstream_artifact_ids"] == ["PRD-OTHER-001"]
