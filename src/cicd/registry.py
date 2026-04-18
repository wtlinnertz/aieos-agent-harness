"""Capability registry — durable adapter registrations backed by an
artifact store with an in-memory index per harness process.

Write-through semantics: a successful register_adapter call persists to the
artifact store before returning. Reads hit the in-memory index first; the
store is the source of truth on process start.

Attestation verification is the gate on registration. In M2.1 the verifier is
a stub that accepts every entry; M2.2 plugs in the real verifier. The
registry exposes the hook so the verifier can be swapped without registry
code changes.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import TYPE_CHECKING

import structlog

from .models import RegistrationOutcome, RegistryEntry

if TYPE_CHECKING:
    from .artifact_store import ArtifactStore

log = structlog.get_logger(__name__)

REGISTRY_PREFIX = "registry"


AttestationVerifier = Callable[[RegistryEntry], RegistrationOutcome]


def _accept_all_stub(_entry: RegistryEntry) -> RegistrationOutcome:
    """Stub verifier used in M2.1. Accepts everything so the registry can be
    exercised before M2.2 plugs in real attestation verification."""
    return RegistrationOutcome(accepted=True, diagnostic="stub verifier (M2.1)")


class CapabilityRegistry:
    """In-memory + artifact-store-backed capability registry.

    The registry keeps an in-memory index keyed by action -> list of entries
    for fast lookup. register_adapter writes through to the artifact store
    and updates the index atomically relative to the process.

    Construction rebuilds the index from the store so state survives process
    restarts.
    """

    def __init__(
        self,
        store: "ArtifactStore",
        attestation_verifier: AttestationVerifier | None = None,
    ) -> None:
        self._store = store
        self._verifier: AttestationVerifier = attestation_verifier or _accept_all_stub
        self._index: dict[str, list[RegistryEntry]] = {}
        self._rebuild_index_from_store()

    # ------------------------------------------------------------------ keys
    @staticmethod
    def _entry_key(adapter_id: str, adapter_version: str) -> str:
        return f"{REGISTRY_PREFIX}/{adapter_id}/{adapter_version}.json"

    # --------------------------------------------------------------- indexing
    def _rebuild_index_from_store(self) -> None:
        """Scan every registry entry in the artifact store into the index.

        Called on __init__ so a fresh process picks up prior registrations.
        """
        self._index = {}
        for key in self._store.list(REGISTRY_PREFIX):
            raw = self._store.get(key)
            if raw is None:
                continue
            try:
                data = json.loads(raw.decode("utf-8"))
                entry = RegistryEntry.from_dict(data)
            except (ValueError, KeyError):
                log.warning("registry_skip_malformed_entry", key=key)
                continue
            for action in entry.capabilities:
                self._index.setdefault(action, []).append(entry)

    def _index_add(self, entry: RegistryEntry) -> None:
        """Add entry to the in-memory index under every action it claims.

        Replaces any existing entry with the same adapter_id + adapter_version
        so re-registration updates rather than duplicates.
        """
        key = (entry.adapter_id, entry.adapter_version)
        for action in entry.capabilities:
            bucket = self._index.setdefault(action, [])
            self._index[action] = [
                e for e in bucket if (e.adapter_id, e.adapter_version) != key
            ]
            self._index[action].append(entry)

    # ----------------------------------------------------------- registration
    def register_adapter(self, entry: RegistryEntry) -> RegistrationOutcome:
        """Verify attestation, persist to store, update index.

        Returns RegistrationOutcome(accepted, diagnostic). On rejection, no
        state is written to the store or the index.
        """
        outcome = self._verifier(entry)
        if not outcome.accepted:
            log.info(
                "registration_rejected",
                adapter_id=entry.adapter_id,
                adapter_version=entry.adapter_version,
                reason=outcome.diagnostic,
            )
            return outcome

        # Write-through to artifact store first. If the store raises, we do
        # NOT update the index — the index must not diverge from the store.
        key = self._entry_key(entry.adapter_id, entry.adapter_version)
        payload = json.dumps(entry.to_dict(), sort_keys=True).encode("utf-8")
        self._store.put(key, payload)

        self._index_add(entry)
        log.info(
            "registration_accepted",
            adapter_id=entry.adapter_id,
            adapter_version=entry.adapter_version,
            capabilities=entry.capabilities,
        )
        return outcome

    # --------------------------------------------------------------- lookups
    def find_adapters(
        self,
        action: str,
        context: dict[str, str] | None = None,
    ) -> list[RegistryEntry]:
        """Return every registered adapter satisfying action.

        context, if provided, narrows the result to entries whose context
        contains the requested tag map as a subset. An entry with a broader
        context still matches as long as every key in the query matches.

        Returns an empty list when no adapter satisfies the action. Never
        falls back to "any adapter" or "first registered" — callers that
        want fallbacks declare them explicitly in a resolver layer above.
        """
        candidates = list(self._index.get(action, []))
        if not context:
            return candidates
        return [
            e
            for e in candidates
            if all(e.context.get(k) == v for k, v in context.items())
        ]

    # ------------------------------------------------------------ inspection
    def all_entries(self) -> list[RegistryEntry]:
        """Every registered entry, de-duplicated by (adapter_id, version)."""
        seen: set[tuple[str, str]] = set()
        unique: list[RegistryEntry] = []
        for bucket in self._index.values():
            for entry in bucket:
                key = (entry.adapter_id, entry.adapter_version)
                if key in seen:
                    continue
                seen.add(key)
                unique.append(entry)
        return unique
