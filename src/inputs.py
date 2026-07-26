"""Declared-inputs resolution (G-3/G-5, manifest 1.1).

``kit-manifest.yml`` artifacts may declare ``inputs: [{ref, role, source}]``
— the non-upstream inputs an artifact needs before generation. Two sources
are resolved here:

* ``framework`` — kit-relative files (the principles seam, G-3). MANDATORY:
  a declared principles file that is missing fails fast, because generating
  without it and then freezing the result is exactly the governance hole the
  gap register documents.
* ``human`` — initiative-relative files (the Path B entry brief, G-5).
  OPTIONAL: entry-path-dependent (Path A places a frozen upstream artifact
  instead), so a missing human input is skipped, not an error.

``upstream`` inputs stay modeled by ``dependency_edges`` + frozen artifacts
and are never resolved here.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import yaml

MANIFEST_RELPATH = Path("aieos-governance-foundation") / "kit-manifest.yml"


def _load_manifest(aieos_root: Path) -> Optional[dict]:
    manifest_path = aieos_root / MANIFEST_RELPATH
    if not manifest_path.exists():
        # No manifest under this root (e.g. minimal test fixtures): nothing
        # is declared, so nothing is resolved. Honest empty, not an error.
        return None
    return yaml.safe_load(manifest_path.read_text(encoding="utf-8"))


def resolve_declared_inputs(
    aieos_root: Path, initiative_path: Path, artifact_type: str
) -> dict[str, str]:
    """Resolve the manifest-declared inputs for one artifact type.

    Returns ``{"<role>: <ref>": <file content>}`` — the same shape adapters
    already render for ``upstream_artifacts``. The artifact is located by its
    convention token (``spec_file == f"{type.lower()}-spec.md"``).
    """
    manifest = _load_manifest(aieos_root)
    if not manifest:
        return {}

    spec_file = f"{artifact_type.lower()}-spec.md"
    kit_repo: Optional[Path] = None
    declared: list[dict] = []
    for kit in manifest.get("kits", {}).values():
        for artifact in kit.get("artifacts", []):
            if artifact.get("spec_file") == spec_file:
                kit_repo = aieos_root / kit["repository"]
                declared = artifact.get("inputs", []) or []
                break
        if kit_repo is not None:
            break

    resolved: dict[str, str] = {}
    for inp in declared:
        ref = inp.get("ref", "")
        role = inp.get("role", "input")
        source = inp.get("source", "")
        if source == "framework":
            path = (kit_repo / ref) if kit_repo else None
            if path is None or not path.exists():
                raise ValueError(
                    f"{artifact_type}: declared framework input not found: "
                    f"{ref} (under {kit_repo}) — refusing to generate without "
                    "its governance inputs (G-3)"
                )
            resolved[f"{role}: {ref}"] = path.read_text(encoding="utf-8")
        elif source == "human":
            path = initiative_path / ref
            if path.exists():
                resolved[f"{role}: {ref}"] = path.read_text(encoding="utf-8")
            # absent human input: entry-path-dependent, skipped by design
    return resolved
