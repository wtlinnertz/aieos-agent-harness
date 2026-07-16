"""Cross-driver initiative lock (FR-019) -- Python side (harness).

A single, whole-initiative ownership lock that all three drivers honor so a dark
factory run and a human in the console cannot write the same initiative's files
at once. This is a *rewrite* of the console's original ``.aieos/lock``, not an
extension (ADR-0002 amendment): the old primitive stored ``hostname`` but its
liveness check ignored it and it had no lease, so a crashed unattended run wedged
the initiative forever and a lock from another host was checked against a random
local PID.

Canonical record: ``.aieos/lock`` (JSON), defined once in
``aieos-schema/schema/lock.yaml`` and implemented identically here and in the
console (TypeScript). Key properties:

- **Hostname-aware liveness.** A PID is only meaningful on the host that wrote
  it, so :func:`_pid_alive` is consulted only when ``hostname`` matches this
  host. Across hosts, staleness is decided purely by the lease. The probe is
  platform-branched: the POSIX ``os.kill(pid, 0)`` idiom is NOT a liveness check
  on Windows -- signal 0 is ``CTRL_C_EVENT`` there (G-15).
- **Lease + heartbeat.** ``renewed_at`` is refreshed on a heartbeat
  (``heartbeat_interval_seconds``, default 60s); a lock older than
  ``lease_ttl_seconds`` (default 300s) is expired and may be taken over, so a
  crashed run frees itself within the TTL instead of wedging forever.
- **session_id is the ownership token.** renew/release verify ``session_id``,
  not PID, so ownership survives cross-host and PID reuse.
- **Stale-lock takeover writes ``.aieos/halt``** (Q6) -- the andon stand-down
  sentinel (ADR-0004), so a prior owner that is somehow still alive stands itself
  down before a human resumes. Takeover is not a freeze and never touches
  ``apply_freeze_decision``.

Granularity is whole-initiative (Q3); handoffs are clean only at frozen artifact
boundaries (documented policy), never mid-convergence.
"""

from __future__ import annotations

import json
import os
import socket
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

LOCK_VERSION = "1.0"
DEFAULT_LEASE_TTL_SECONDS = 300  # 5 min (Q4)
DEFAULT_HEARTBEAT_SECONDS = 60  # 60 s (Q4)

_VALID_DRIVERS = frozenset({"console", "dark-factory", "sherpa"})


@dataclass
class LockInfo:
    """The on-disk ``.aieos/lock`` record. Mirrors aieos-schema/schema/lock.yaml."""

    owner: str
    driver: str
    session_id: str
    hostname: str
    pid: int
    acquired_at: str
    renewed_at: str
    initiative: str = ""
    lease_ttl_seconds: int = DEFAULT_LEASE_TTL_SECONDS
    heartbeat_interval_seconds: int = DEFAULT_HEARTBEAT_SECONDS
    lock_version: str = LOCK_VERSION


@dataclass
class LockResult:
    """Outcome of :func:`acquire_lock`.

    - ``acquired`` -- True if the caller now holds the lock.
    - ``took_over`` -- True if acquisition displaced an expired/stale prior owner
      (in which case a ``.aieos/halt`` sentinel was written).
    - ``info`` -- the lock the caller now holds (on success), else the live
      blocking lock (on failure).
    - ``previous`` -- the displaced lock, when ``took_over`` is True.
    """

    acquired: bool
    took_over: bool = False
    info: Optional[LockInfo] = None
    previous: Optional[LockInfo] = None


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _fmt(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse(ts: str) -> datetime:
    # Tolerant of both the canonical no-millis form and an ISO variant with a
    # fractional part or offset, so a lock written by either language reads back.
    ts = ts.strip()
    if ts.endswith("Z"):
        ts = ts[:-1]
    if "." in ts:
        ts = ts.split(".", 1)[0]
    return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)


def _lock_path(initiative_path: Path) -> Path:
    return Path(initiative_path) / ".aieos" / "lock"


def _halt_path(initiative_path: Path) -> Path:
    return Path(initiative_path) / ".aieos" / "halt"


def _pid_alive_windows(pid: int) -> bool:
    """Windows liveness probe (G-15).

    ``os.kill(pid, 0)`` MUST NOT be used here. On Windows ``signal.CTRL_C_EVENT``
    is 0, and ``os.kill`` maps signal 0 to
    ``GenerateConsoleCtrlEvent(CTRL_C_EVENT, pid)`` -- it does not probe the
    process, it fires a real Ctrl-C at the console process group. Used as a
    liveness check that means a stale-lock probe would kill the harness, the
    console's harness subprocess, or the user's shell: a lock that terminates
    whoever asks about the lock. Query the process handle instead.
    """
    import ctypes
    from ctypes import wintypes

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    ERROR_ACCESS_DENIED = 5
    STILL_ACTIVE = 259

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        # Exists but we lack rights to query it -- still alive. Mirrors the
        # POSIX PermissionError branch below.
        return ctypes.get_last_error() == ERROR_ACCESS_DENIED
    try:
        code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return False
        return code.value == STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def _pid_alive(pid: int) -> bool:
    """True if a process with ``pid`` exists on THIS host (advisory).

    Platform-branched deliberately: the POSIX signal-0 idiom is actively
    dangerous on Windows (see :func:`_pid_alive_windows`).
    """
    if pid <= 0:
        return False
    if os.name == "nt":
        return _pid_alive_windows(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Exists but owned by another user -- still alive.
        return True
    except OSError:
        return False
    return True


def read_lock(initiative_path: Path) -> Optional[LockInfo]:
    """Return the current lock record, or None if the initiative is unlocked."""
    lock_file = _lock_path(initiative_path)
    if not lock_file.exists():
        return None
    data = json.loads(lock_file.read_text(encoding="utf-8"))
    known = {f for f in LockInfo.__dataclass_fields__}  # type: ignore[attr-defined]
    return LockInfo(**{k: v for k, v in data.items() if k in known})


def is_takeable(
    info: LockInfo,
    *,
    now: Optional[datetime] = None,
    hostname: Optional[str] = None,
    pid_is_alive: Callable[[int], bool] = _pid_alive,
) -> bool:
    """Whether an existing lock may be taken over.

    Takeable when the lease has expired (``now - renewed_at > lease_ttl``) OR the
    lock was written on THIS host and its PID is no longer alive (fast path). A
    lock from another host that is still within its lease is never takeable here
    -- only the lease can free it, since its PID is meaningless locally.
    """
    now = now or _utcnow()
    this_host = hostname if hostname is not None else socket.gethostname()
    age = (now - _parse(info.renewed_at)).total_seconds()
    if age > info.lease_ttl_seconds:
        return True
    if info.hostname == this_host and not pid_is_alive(info.pid):
        return True
    return False


def _write_lock(lock_file: Path, info: LockInfo) -> None:
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    lock_file.write_text(json.dumps(asdict(info), indent=2), encoding="utf-8")


def _write_halt_sentinel(
    initiative_path: Path, previous: LockInfo, taker: LockInfo, now: datetime
) -> None:
    """Write the andon stand-down sentinel on a stale-lock takeover (Q6, ADR-0004).

    A prior owner that is still alive checks this before each artifact and stands
    itself down, so two drivers never proceed against one initiative.
    """
    payload = {
        "reason": "stale_lock_takeover",
        "at": _fmt(now),
        "taken_from": {
            "owner": previous.owner,
            "driver": previous.driver,
            "session_id": previous.session_id,
            "hostname": previous.hostname,
            "pid": previous.pid,
        },
        "taken_by": {
            "owner": taker.owner,
            "driver": taker.driver,
            "session_id": taker.session_id,
            "hostname": taker.hostname,
        },
    }
    halt = _halt_path(initiative_path)
    halt.parent.mkdir(parents=True, exist_ok=True)
    halt.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def acquire_lock(
    initiative_path: Path,
    owner: str,
    driver: str,
    *,
    session_id: Optional[str] = None,
    initiative: str = "",
    lease_ttl_seconds: int = DEFAULT_LEASE_TTL_SECONDS,
    heartbeat_interval_seconds: int = DEFAULT_HEARTBEAT_SECONDS,
    now: Optional[datetime] = None,
    hostname: Optional[str] = None,
    pid: Optional[int] = None,
    pid_is_alive: Callable[[int], bool] = _pid_alive,
) -> LockResult:
    """Acquire the whole-initiative lock.

    Returns a :class:`LockResult`. Acquisition succeeds when the initiative is
    unlocked, already held by this ``session_id`` (a renewing re-acquire), or the
    existing lock is takeable (expired lease, or same-host dead PID) -- in which
    case the prior lock is displaced and a ``.aieos/halt`` sentinel is written.
    A live, unexpired lock held by another session blocks: ``acquired=False`` and
    the blocking lock is returned in ``info``.
    """
    if driver not in _VALID_DRIVERS:
        raise ValueError(
            f"Unknown driver {driver!r}; expected one of {sorted(_VALID_DRIVERS)}"
        )

    now = now or _utcnow()
    this_host = hostname if hostname is not None else socket.gethostname()
    this_pid = pid if pid is not None else os.getpid()
    sid = session_id or uuid.uuid4().hex
    lock_file = _lock_path(initiative_path)

    existing = read_lock(initiative_path)

    if existing is not None and existing.session_id == sid:
        # Re-acquire our own lock: refresh the heartbeat.
        existing.renewed_at = _fmt(now)
        _write_lock(lock_file, existing)
        return LockResult(acquired=True, took_over=False, info=existing)

    if existing is not None:
        if not is_takeable(
            existing, now=now, hostname=this_host, pid_is_alive=pid_is_alive
        ):
            # Held by a live, unexpired other owner.
            return LockResult(acquired=False, took_over=False, info=existing)

    ours = LockInfo(
        owner=owner,
        driver=driver,
        session_id=sid,
        hostname=this_host,
        pid=this_pid,
        acquired_at=_fmt(now),
        renewed_at=_fmt(now),
        initiative=initiative,
        lease_ttl_seconds=lease_ttl_seconds,
        heartbeat_interval_seconds=heartbeat_interval_seconds,
    )

    took_over = existing is not None
    if took_over:
        _write_halt_sentinel(initiative_path, existing, ours, now)

    _write_lock(lock_file, ours)
    return LockResult(
        acquired=True,
        took_over=took_over,
        info=ours,
        previous=existing if took_over else None,
    )


def renew_lock(
    initiative_path: Path,
    session_id: str,
    *,
    now: Optional[datetime] = None,
) -> bool:
    """Refresh the heartbeat (``renewed_at``) if we still own the lock.

    Returns False if the lock is gone or now held by another session -- the
    caller has lost the initiative and must stop.
    """
    now = now or _utcnow()
    existing = read_lock(initiative_path)
    if existing is None or existing.session_id != session_id:
        return False
    existing.renewed_at = _fmt(now)
    _write_lock(_lock_path(initiative_path), existing)
    return True


def release_lock(initiative_path: Path, session_id: str) -> bool:
    """Release the lock iff we own it. Returns True if we removed our lock.

    A no-op (returns False) when there is no lock or it belongs to another
    session -- never steals another owner's lock.
    """
    existing = read_lock(initiative_path)
    if existing is None or existing.session_id != session_id:
        return False
    _lock_path(initiative_path).unlink()
    return True
