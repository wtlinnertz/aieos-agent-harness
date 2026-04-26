"""Operator CLI for the AIEOS capability registry.

Lets operators register, list, and inspect adapters in a filesystem-backed
harness store without writing custom Python. The CLI is the v1 surface for
the registration workflow described in docs/operator-guide.md.

Usage:
    python -m src.cicd.cli register \\
        --adapter-id adapter-pytest-unit \\
        --adapter-version 1.0.0 \\
        --capability test.unit \\
        --contract-version 1.0.0 \\
        --attestation /path/to/bundle.sigstore.json \\
        --signing-identity 'https://github.com/.../ci.yml@refs/heads/main' \\
        --store /var/lib/aieos/harness-registry \\
        --bundle-wrapped         # bundle is a Sigstore wrapper

    python -m src.cicd.cli list --store /var/lib/aieos/harness-registry

    python -m src.cicd.cli show \\
        --adapter-id adapter-pytest-unit \\
        --store /var/lib/aieos/harness-registry

The CLI is intentionally minimal — registration is a governance action,
not a self-service endpoint. It exists to make the M2 register_adapter API
ergonomic for operators running registrations from a script.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import TextIO

from .artifact_store import FilesystemArtifactStore
from .attestation import (
    AttestationVerifier,
    ContractRegistration,
    _file_uri_fetcher,
    bundle_unwrapping_fetcher,
)
from .models import HealthStatus, RegistryEntry
from .registry import CapabilityRegistry


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="aieos-harness")
    sub = p.add_subparsers(dest="cmd", required=True)

    reg = sub.add_parser(
        "register", help="Register an adapter via its conformance attestation"
    )
    reg.add_argument("--adapter-id", required=True)
    reg.add_argument("--adapter-version", required=True)
    reg.add_argument(
        "--capability", required=True, help="Action identifier the adapter claims"
    )
    reg.add_argument("--contract-version", default="1.0.0")
    reg.add_argument("--attestation", required=True, help="Path to attestation file")
    reg.add_argument(
        "--bundle-wrapped",
        action="store_true",
        help="Attestation is a Sigstore bundle that needs unwrapping",
    )
    reg.add_argument("--signing-identity", required=True)
    reg.add_argument(
        "--store", required=True, help="Filesystem artifact-store directory"
    )
    reg.add_argument(
        "--context",
        action="append",
        default=[],
        help="key=value context tag (repeat for multiple)",
    )
    reg.add_argument(
        "--health-status", default="healthy", choices=[s.value for s in HealthStatus]
    )

    lst = sub.add_parser("list", help="List registered adapters")
    lst.add_argument("--store", required=True)
    lst.add_argument("--action", help="Filter by action identifier")

    show = sub.add_parser("show", help="Show registered adapter details")
    show.add_argument("--store", required=True)
    show.add_argument("--adapter-id", required=True)
    show.add_argument("--adapter-version", required=False)

    return p


def _build_registry(
    store_dir: Path,
    *,
    adapter_id: str | None = None,
    capability: str | None = None,
    contract_version: str | None = None,
    signing_identity: str | None = None,
    bundle_wrapped: bool = False,
) -> CapabilityRegistry:
    store = FilesystemArtifactStore(store_dir)
    if capability and contract_version and signing_identity:
        verifier = AttestationVerifier(
            trusted_identities={signing_identity},
            contract_registrations={
                capability: ContractRegistration(current_version=contract_version),
            },
            attestation_fetcher=(
                bundle_unwrapping_fetcher(_file_uri_fetcher)
                if bundle_wrapped
                else _file_uri_fetcher
            ),
        )
        return CapabilityRegistry(store=store, attestation_verifier=verifier)
    return CapabilityRegistry(store=store)


def cmd_register(
    args: argparse.Namespace,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    out = stdout if stdout is not None else sys.stdout
    err = stderr if stderr is not None else sys.stderr

    attestation_path = Path(args.attestation).resolve()
    if not attestation_path.is_file():
        err.write(f"[error] attestation file not found: {attestation_path}\n")
        return 2

    context: dict[str, str] = {}
    for kv in args.context:
        if "=" not in kv:
            err.write(f"[error] invalid context entry {kv!r} (expected key=value)\n")
            return 2
        k, _, v = kv.partition("=")
        context[k] = v

    registry = _build_registry(
        Path(args.store),
        capability=args.capability,
        contract_version=args.contract_version,
        signing_identity=args.signing_identity,
        bundle_wrapped=args.bundle_wrapped,
    )
    entry = RegistryEntry(
        adapter_id=args.adapter_id,
        adapter_version=args.adapter_version,
        capabilities=[args.capability],
        contract_versions={args.capability: args.contract_version},
        attestation_ref=f"file://{attestation_path}",
        registered_at=datetime.now(UTC),
        context=context,
        health_status=HealthStatus(args.health_status),
    )
    outcome = registry.register_adapter(entry)
    if not outcome.accepted:
        err.write(f"[refused] {outcome.diagnostic}\n")
        return 1
    out.write(
        json.dumps(
            {
                "registered": {
                    "adapter_id": entry.adapter_id,
                    "adapter_version": entry.adapter_version,
                    "capability": args.capability,
                    "diagnostic": outcome.diagnostic,
                }
            },
            indent=2,
        )
        + "\n"
    )
    return 0


def cmd_list(
    args: argparse.Namespace,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    out = stdout if stdout is not None else sys.stdout
    registry = _build_registry(Path(args.store))
    entries = registry.all_entries()
    if args.action:
        entries = [e for e in entries if args.action in e.capabilities]
    payload = [
        {
            "adapter_id": e.adapter_id,
            "adapter_version": e.adapter_version,
            "capabilities": list(e.capabilities),
            "context": dict(e.context),
            "health_status": e.health_status.value,
        }
        for e in entries
    ]
    out.write(json.dumps({"count": len(payload), "entries": payload}, indent=2) + "\n")
    return 0


def cmd_show(
    args: argparse.Namespace,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    out = stdout if stdout is not None else sys.stdout
    err = stderr if stderr is not None else sys.stderr
    registry = _build_registry(Path(args.store))
    matches = [
        e
        for e in registry.all_entries()
        if e.adapter_id == args.adapter_id
        and (args.adapter_version is None or e.adapter_version == args.adapter_version)
    ]
    if not matches:
        err.write(
            f"[error] no entries match {args.adapter_id}@{args.adapter_version or '*'}\n"
        )
        return 1
    out.write(json.dumps([e.to_dict() for e in matches], indent=2) + "\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.cmd == "register":
        return cmd_register(args)
    if args.cmd == "list":
        return cmd_list(args)
    if args.cmd == "show":
        return cmd_show(args)
    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
