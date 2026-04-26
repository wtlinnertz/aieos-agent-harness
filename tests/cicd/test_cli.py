"""Track H — operator CLI tests."""

from __future__ import annotations

import io
import json
from datetime import UTC, datetime

from src.cicd.cli import _build_parser, cmd_list, cmd_register, cmd_show


def _build_attestation_payload(
    *, adapter_id: str = "adapter-pytest-unit", contract_id: str = "test.unit"
) -> dict:
    return {
        "subject": {"adapter_id": adapter_id, "adapter_version": "1.0.0"},
        "predicate": {"contract_id": contract_id, "contract_version": "1.0.0"},
        "suite_run_id": "550e8400-e29b-41d4-a716-446655440000",
        "result": "pass",
        "signing_identity": "https://github.com/wtlinnertz/x@main",
        "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    }


def test_register_happy_path(tmp_path):
    """End-to-end: write a payload, run register, see it in the store."""
    payload_path = tmp_path / "att.json"
    payload_path.write_text(json.dumps(_build_attestation_payload()))
    store_dir = tmp_path / "store"

    parser = _build_parser()
    args = parser.parse_args(
        [
            "register",
            "--adapter-id",
            "adapter-pytest-unit",
            "--adapter-version",
            "1.0.0",
            "--capability",
            "test.unit",
            "--contract-version",
            "1.0.0",
            "--attestation",
            str(payload_path),
            "--signing-identity",
            "https://github.com/wtlinnertz/x@main",
            "--store",
            str(store_dir),
        ]
    )
    out, err = io.StringIO(), io.StringIO()
    code = cmd_register(args, stdout=out, stderr=err)
    assert code == 0, err.getvalue()
    parsed = json.loads(out.getvalue())
    assert parsed["registered"]["adapter_id"] == "adapter-pytest-unit"
    # Persisted to the store
    assert (store_dir / "registry" / "adapter-pytest-unit" / "1.0.0.json").is_file()


def test_register_with_context_tags(tmp_path):
    payload_path = tmp_path / "att.json"
    payload_path.write_text(json.dumps(_build_attestation_payload()))
    store_dir = tmp_path / "store"

    parser = _build_parser()
    args = parser.parse_args(
        [
            "register",
            "--adapter-id",
            "adapter-pytest-unit",
            "--adapter-version",
            "1.0.0",
            "--capability",
            "test.unit",
            "--attestation",
            str(payload_path),
            "--signing-identity",
            "https://github.com/wtlinnertz/x@main",
            "--store",
            str(store_dir),
            "--context",
            "environment=ci",
            "--context",
            "team=platform",
        ]
    )
    out, err = io.StringIO(), io.StringIO()
    code = cmd_register(args, stdout=out, stderr=err)
    assert code == 0


def test_register_missing_attestation_file(tmp_path):
    parser = _build_parser()
    args = parser.parse_args(
        [
            "register",
            "--adapter-id",
            "x",
            "--adapter-version",
            "1.0.0",
            "--capability",
            "test.unit",
            "--attestation",
            str(tmp_path / "missing.json"),
            "--signing-identity",
            "x",
            "--store",
            str(tmp_path / "store"),
        ]
    )
    err = io.StringIO()
    code = cmd_register(args, stdout=io.StringIO(), stderr=err)
    assert code == 2
    assert "not found" in err.getvalue()


def test_register_refused_with_diagnostic(tmp_path):
    """Trusted identity mismatch surfaces the verifier's diagnostic."""
    payload_path = tmp_path / "att.json"
    payload_path.write_text(json.dumps(_build_attestation_payload()))
    store_dir = tmp_path / "store"

    parser = _build_parser()
    args = parser.parse_args(
        [
            "register",
            "--adapter-id",
            "adapter-pytest-unit",
            "--adapter-version",
            "1.0.0",
            "--capability",
            "test.unit",
            "--attestation",
            str(payload_path),
            "--signing-identity",
            "https://different-identity@main",  # mismatches the payload
            "--store",
            str(store_dir),
        ]
    )
    err = io.StringIO()
    code = cmd_register(args, stdout=io.StringIO(), stderr=err)
    assert code == 1
    # Underlying diagnostic surfaces
    assert "trusted set" in err.getvalue()


def test_list_after_register(tmp_path):
    """Register then list — entries appear."""
    payload_path = tmp_path / "att.json"
    payload_path.write_text(json.dumps(_build_attestation_payload()))
    store_dir = tmp_path / "store"

    parser = _build_parser()
    reg_args = parser.parse_args(
        [
            "register",
            "--adapter-id",
            "adapter-pytest-unit",
            "--adapter-version",
            "1.0.0",
            "--capability",
            "test.unit",
            "--attestation",
            str(payload_path),
            "--signing-identity",
            "https://github.com/wtlinnertz/x@main",
            "--store",
            str(store_dir),
        ]
    )
    cmd_register(reg_args, stdout=io.StringIO(), stderr=io.StringIO())

    list_args = parser.parse_args(["list", "--store", str(store_dir)])
    out = io.StringIO()
    code = cmd_list(list_args, stdout=out, stderr=io.StringIO())
    assert code == 0
    parsed = json.loads(out.getvalue())
    assert parsed["count"] == 1
    assert parsed["entries"][0]["adapter_id"] == "adapter-pytest-unit"


def test_list_filters_by_action(tmp_path):
    payload_path = tmp_path / "att.json"
    payload_path.write_text(json.dumps(_build_attestation_payload()))
    store_dir = tmp_path / "store"

    parser = _build_parser()
    cmd_register(
        parser.parse_args(
            [
                "register",
                "--adapter-id",
                "adapter-pytest-unit",
                "--adapter-version",
                "1.0.0",
                "--capability",
                "test.unit",
                "--attestation",
                str(payload_path),
                "--signing-identity",
                "https://github.com/wtlinnertz/x@main",
                "--store",
                str(store_dir),
            ]
        ),
        stdout=io.StringIO(),
        stderr=io.StringIO(),
    )

    list_args = parser.parse_args(
        ["list", "--store", str(store_dir), "--action", "security.dast"]
    )
    out = io.StringIO()
    cmd_list(list_args, stdout=out, stderr=io.StringIO())
    parsed = json.loads(out.getvalue())
    assert parsed["count"] == 0


def test_show_returns_full_entry(tmp_path):
    payload_path = tmp_path / "att.json"
    payload_path.write_text(json.dumps(_build_attestation_payload()))
    store_dir = tmp_path / "store"

    parser = _build_parser()
    cmd_register(
        parser.parse_args(
            [
                "register",
                "--adapter-id",
                "adapter-pytest-unit",
                "--adapter-version",
                "1.0.0",
                "--capability",
                "test.unit",
                "--attestation",
                str(payload_path),
                "--signing-identity",
                "https://github.com/wtlinnertz/x@main",
                "--store",
                str(store_dir),
            ]
        ),
        stdout=io.StringIO(),
        stderr=io.StringIO(),
    )

    show_args = parser.parse_args(
        ["show", "--store", str(store_dir), "--adapter-id", "adapter-pytest-unit"]
    )
    out = io.StringIO()
    cmd_show(show_args, stdout=out, stderr=io.StringIO())
    parsed = json.loads(out.getvalue())
    assert parsed[0]["adapter_id"] == "adapter-pytest-unit"
    assert "registered_at" in parsed[0]


def test_show_missing_adapter_returns_error(tmp_path):
    parser = _build_parser()
    args = parser.parse_args(
        [
            "show",
            "--store",
            str(tmp_path / "empty-store"),
            "--adapter-id",
            "adapter-nonexistent",
        ]
    )
    err = io.StringIO()
    code = cmd_show(args, stdout=io.StringIO(), stderr=err)
    assert code == 1
    assert "no entries match" in err.getvalue()
