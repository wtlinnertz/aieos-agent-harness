"""Artifact store protocol for the capability registry.

The harness depends on an ArtifactStore abstraction: an opaque key-value store
for durable registry state. A filesystem-backed implementation lives here and
is used in tests. The production path resolves to aieos-artifact-store via
configuration at integration time.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol


class ArtifactStore(Protocol):
    """Key-value artifact store.

    Implementations must be thread-safe enough to survive concurrent
    registrations in a single harness process. Durability across process
    restarts is the implementation's responsibility — the in-memory index
    rebuilds from the store on construction.
    """

    def put(self, key: str, content: bytes) -> None:
        """Write content under key. Overwrites prior value at the same key."""

    def get(self, key: str) -> bytes | None:
        """Read content at key, or None if absent."""

    def list(self, prefix: str) -> list[str]:
        """Return every key that begins with prefix."""

    def delete(self, key: str) -> None:
        """Remove content at key. No-op if absent."""


class FilesystemArtifactStore:
    """Filesystem-backed ArtifactStore — used in tests and for local dev.

    Keys map to files under root/; directory structure mirrors key segments
    separated by '/'. The production store resolves keys through a different
    backend; the contract is identical.
    """

    def __init__(self, root: Path) -> None:
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)

    def _path_for(self, key: str) -> Path:
        if key.startswith("/") or ".." in key.split("/"):
            raise ValueError(f"invalid key: {key!r}")
        return self._root / key

    def put(self, key: str, content: bytes) -> None:
        path = self._path_for(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    def get(self, key: str) -> bytes | None:
        path = self._path_for(key)
        if not path.is_file():
            return None
        return path.read_bytes()

    def list(self, prefix: str) -> list[str]:
        base = self._root / prefix
        if not base.exists():
            return []
        keys: list[str] = []
        if base.is_file():
            keys.append(prefix)
            return keys
        for p in base.rglob("*"):
            if p.is_file():
                keys.append(p.relative_to(self._root).as_posix())
        return sorted(keys)

    def delete(self, key: str) -> None:
        path = self._path_for(key)
        if path.is_file():
            path.unlink()
