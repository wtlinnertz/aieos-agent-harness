"""Tests for the cross-driver initiative lock (FR-019, Python side)."""

import json
import os
from datetime import datetime, timedelta, timezone

import pytest

from src.lock import (
    DEFAULT_LEASE_TTL_SECONDS,
    LockInfo,
    acquire_lock,
    is_takeable,
    read_lock,
    release_lock,
    renew_lock,
)

T0 = datetime(2026, 7, 12, 10, 0, 0, tzinfo=timezone.utc)
HOST = "host-A"


def _dead(pid):
    return False


def _alive(pid):
    return True


def _acquire(tmp_path, **kw):
    kw.setdefault("owner", "todd")
    kw.setdefault("driver", "dark-factory")
    kw.setdefault("now", T0)
    kw.setdefault("hostname", HOST)
    kw.setdefault("pid", 4242)
    kw.setdefault("pid_is_alive", _alive)
    return acquire_lock(tmp_path, **kw)


class TestAcquireFresh:
    def test_acquire_on_unlocked_initiative(self, tmp_path):
        r = _acquire(tmp_path, session_id="s1")
        assert r.acquired is True
        assert r.took_over is False
        assert r.info.session_id == "s1"
        assert (tmp_path / ".aieos" / "lock").exists()

    def test_lock_record_fields(self, tmp_path):
        _acquire(tmp_path, session_id="s1", initiative="INIT-S-005")
        info = read_lock(tmp_path)
        assert info.owner == "todd"
        assert info.driver == "dark-factory"
        assert info.hostname == HOST
        assert info.pid == 4242
        assert info.initiative == "INIT-S-005"
        assert info.lease_ttl_seconds == DEFAULT_LEASE_TTL_SECONDS
        assert info.acquired_at == "2026-07-12T10:00:00Z"
        assert info.lock_version == "1.0"

    def test_generates_session_id_when_absent(self, tmp_path):
        r = _acquire(tmp_path)
        assert r.info.session_id  # non-empty uuid

    def test_rejects_unknown_driver(self, tmp_path):
        with pytest.raises(ValueError, match="Unknown driver"):
            _acquire(tmp_path, driver="wizard")


class TestAcquireContended:
    def test_blocked_by_live_unexpired_other(self, tmp_path):
        _acquire(tmp_path, session_id="s1")
        # different session, same host, pid alive, only 10s later
        r = _acquire(
            tmp_path, session_id="s2", now=T0 + timedelta(seconds=10), pid_is_alive=_alive
        )
        assert r.acquired is False
        assert r.info.session_id == "s1"  # blocker returned
        # no halt sentinel on a clean block
        assert not (tmp_path / ".aieos" / "halt").exists()

    def test_reacquire_own_session_refreshes_heartbeat(self, tmp_path):
        _acquire(tmp_path, session_id="s1")
        r = _acquire(tmp_path, session_id="s1", now=T0 + timedelta(seconds=30))
        assert r.acquired is True
        assert r.took_over is False
        assert read_lock(tmp_path).renewed_at == "2026-07-12T10:00:30Z"


class TestTakeover:
    def test_expired_lease_taken_over_with_halt(self, tmp_path):
        _acquire(tmp_path, session_id="s1")
        later = T0 + timedelta(seconds=DEFAULT_LEASE_TTL_SECONDS + 1)
        r = _acquire(tmp_path, session_id="s2", now=later, pid_is_alive=_alive)
        assert r.acquired is True
        assert r.took_over is True
        assert r.previous.session_id == "s1"
        assert read_lock(tmp_path).session_id == "s2"
        # andon stand-down sentinel written
        halt = json.loads((tmp_path / ".aieos" / "halt").read_text())
        assert halt["reason"] == "stale_lock_takeover"
        assert halt["taken_from"]["session_id"] == "s1"
        assert halt["taken_by"]["session_id"] == "s2"

    def test_same_host_dead_pid_taken_over_before_lease(self, tmp_path):
        _acquire(tmp_path, session_id="s1", pid=4242)
        # 10s later (well within lease) but the owner PID is dead on this host
        r = _acquire(
            tmp_path, session_id="s2", now=T0 + timedelta(seconds=10), pid_is_alive=_dead
        )
        assert r.acquired is True
        assert r.took_over is True

    def test_cross_host_unexpired_not_taken_over(self, tmp_path):
        _acquire(tmp_path, session_id="s1", hostname="host-A", pid=4242)
        # a DIFFERENT host cannot judge host-A's pid; within lease -> blocked
        r = acquire_lock(
            tmp_path, owner="ci", driver="console", session_id="s2",
            now=T0 + timedelta(seconds=10), hostname="host-B", pid=99,
            pid_is_alive=_dead,  # would be "dead" locally, but must be ignored cross-host
        )
        assert r.acquired is False
        assert r.info.session_id == "s1"


class TestIsTakeable:
    def _info(self, **kw):
        base = dict(
            owner="t", driver="dark-factory", session_id="s", hostname=HOST,
            pid=4242, acquired_at="2026-07-12T10:00:00Z",
            renewed_at="2026-07-12T10:00:00Z",
        )
        base.update(kw)
        return LockInfo(**base)

    def test_expired_is_takeable(self):
        info = self._info()
        assert is_takeable(
            info, now=T0 + timedelta(seconds=DEFAULT_LEASE_TTL_SECONDS + 1),
            hostname=HOST, pid_is_alive=_alive,
        ) is True

    def test_fresh_live_not_takeable(self):
        info = self._info()
        assert is_takeable(
            info, now=T0 + timedelta(seconds=5), hostname=HOST, pid_is_alive=_alive
        ) is False

    def test_default_hostname_path(self):
        # exercise the socket.gethostname() default branch (no hostname kw)
        info = self._info(hostname="definitely-not-this-host")
        # cross-host + fresh -> not takeable regardless of pid
        assert is_takeable(info, now=T0 + timedelta(seconds=5)) is False


class TestRenew:
    def test_renew_own_advances_heartbeat(self, tmp_path):
        _acquire(tmp_path, session_id="s1")
        ok = renew_lock(tmp_path, "s1", now=T0 + timedelta(seconds=60))
        assert ok is True
        assert read_lock(tmp_path).renewed_at == "2026-07-12T10:01:00Z"

    def test_renew_foreign_session_fails(self, tmp_path):
        _acquire(tmp_path, session_id="s1")
        assert renew_lock(tmp_path, "other", now=T0) is False

    def test_renew_no_lock_fails(self, tmp_path):
        assert renew_lock(tmp_path, "s1", now=T0) is False


class TestRelease:
    def test_release_own(self, tmp_path):
        _acquire(tmp_path, session_id="s1")
        assert release_lock(tmp_path, "s1") is True
        assert read_lock(tmp_path) is None

    def test_release_foreign_is_noop(self, tmp_path):
        _acquire(tmp_path, session_id="s1")
        assert release_lock(tmp_path, "other") is False
        assert read_lock(tmp_path).session_id == "s1"

    def test_release_no_lock(self, tmp_path):
        assert release_lock(tmp_path, "s1") is False


class TestReadLock:
    def test_read_none_when_absent(self, tmp_path):
        assert read_lock(tmp_path) is None

    def test_read_ignores_unknown_fields(self, tmp_path):
        d = tmp_path / ".aieos"
        d.mkdir(parents=True)
        (d / "lock").write_text(json.dumps({
            "owner": "t", "driver": "console", "session_id": "s", "hostname": HOST,
            "pid": 1, "acquired_at": "2026-07-12T10:00:00Z",
            "renewed_at": "2026-07-12T10:00:00Z", "future_field": "ignored",
        }))
        info = read_lock(tmp_path)
        assert info.owner == "t"


class TestPidAliveHelper:
    def test_current_process_is_alive(self):
        import os
        from src.lock import _pid_alive
        assert _pid_alive(os.getpid()) is True

    def test_nonpositive_pid_dead(self):
        from src.lock import _pid_alive
        assert _pid_alive(0) is False

    def test_unlikely_pid_dead(self):
        from src.lock import _pid_alive
        assert _pid_alive(2_000_000_000) is False


class TestPidAliveBranches:
    """POSIX branch. Pinned to os.name == 'posix' so these still exercise the
    signal-0 path when the suite runs on Windows (G-15 platform-branched it)."""

    def test_process_lookup_is_dead(self, monkeypatch):
        import src.lock as lock
        monkeypatch.setattr(lock.os, "name", "posix")
        def boom(pid, sig):
            raise ProcessLookupError
        monkeypatch.setattr(lock.os, "kill", boom)
        assert lock._pid_alive(123) is False

    def test_permission_error_is_alive(self, monkeypatch):
        import src.lock as lock
        monkeypatch.setattr(lock.os, "name", "posix")
        def boom(pid, sig):
            raise PermissionError
        monkeypatch.setattr(lock.os, "kill", boom)
        assert lock._pid_alive(123) is True

    def test_generic_oserror_is_dead(self, monkeypatch):
        import src.lock as lock
        monkeypatch.setattr(lock.os, "name", "posix")
        def boom(pid, sig):
            raise OSError
        monkeypatch.setattr(lock.os, "kill", boom)
        assert lock._pid_alive(123) is False


class TestPidAliveNeverSignalsOnWindows:
    """G-13's sibling, G-15 -- the regression guard that matters.

    ``os.kill(pid, 0)`` is the POSIX "does this process exist?" no-op probe. On
    Windows ``signal.CTRL_C_EVENT == 0``, so the same call fires a real Ctrl-C at
    the console process group instead of probing. As a lock liveness check that
    means a stale-lock probe kills the harness, the console's harness subprocess,
    or the user's shell.

    Symptom that exposed it: ``pytest`` died with KeyboardInterrupt at ~78% with
    nobody touching the keyboard, at a different traceback location each run.

    These run on every platform: they assert the *dispatch*, not the probe, so a
    Linux CI box still fails if someone deletes the platform branch.
    """

    def test_windows_dispatch_never_calls_os_kill(self, monkeypatch):
        import src.lock as lock
        monkeypatch.setattr(lock.os, "name", "nt")
        monkeypatch.setattr(lock, "_pid_alive_windows", lambda pid: True)

        def forbidden(pid, sig):
            raise AssertionError(
                "os.kill(pid, 0) on Windows sends CTRL_C_EVENT to the console "
                "process group -- it is not a liveness probe (G-15)"
            )

        monkeypatch.setattr(lock.os, "kill", forbidden)
        assert lock._pid_alive(123) is True

    def test_windows_dispatch_reaches_the_handle_probe(self, monkeypatch):
        import src.lock as lock
        seen = []
        monkeypatch.setattr(lock.os, "name", "nt")
        monkeypatch.setattr(
            lock, "_pid_alive_windows", lambda pid: seen.append(pid) or False
        )
        assert lock._pid_alive(4321) is False
        assert seen == [4321]

    def test_nonpositive_pid_short_circuits_before_any_probe(self, monkeypatch):
        """pid <= 0 must never reach a probe: GenerateConsoleCtrlEvent(pid=0)
        targets EVERY process sharing the console."""
        import src.lock as lock
        monkeypatch.setattr(lock.os, "name", "nt")

        def forbidden(pid):  # pragma: no cover - must never run
            raise AssertionError("probed a non-positive pid")

        monkeypatch.setattr(lock, "_pid_alive_windows", forbidden)
        assert lock._pid_alive(0) is False
        assert lock._pid_alive(-1) is False


@pytest.mark.skipif(os.name != "nt", reason="Windows-only handle probe")
class TestPidAliveWindowsProbe:
    def test_current_process_alive(self):
        from src.lock import _pid_alive_windows
        assert _pid_alive_windows(os.getpid()) is True

    def test_unlikely_pid_dead(self):
        from src.lock import _pid_alive_windows
        assert _pid_alive_windows(2_000_000_000) is False


class TestDefaultsBranches:
    def test_acquire_without_injected_now(self, tmp_path):
        # exercises the real _utcnow() / socket.gethostname() / os.getpid() defaults
        from src.lock import acquire_lock, read_lock
        r = acquire_lock(tmp_path, owner="todd", driver="sherpa", session_id="s1")
        assert r.acquired is True
        info = read_lock(tmp_path)
        assert info.pid > 0
        assert info.hostname  # non-empty
        assert info.renewed_at.endswith("Z")


class TestParseTolerance:
    def test_reads_millisecond_timestamp(self, tmp_path):
        # A record written with JS-style millis must still parse (cross-driver).
        import json
        from src.lock import read_lock, is_takeable
        d = tmp_path / ".aieos"; d.mkdir(parents=True)
        (d / "lock").write_text(json.dumps({
            "owner": "t", "driver": "console", "session_id": "s", "hostname": HOST,
            "pid": 1, "acquired_at": "2026-07-12T10:00:00.123Z",
            "renewed_at": "2026-07-12T10:00:00.123Z",
            "lease_ttl_seconds": 300, "heartbeat_interval_seconds": 60,
            "lock_version": "1.0",
        }))
        info = read_lock(tmp_path)
        # fresh -> not takeable, proving renewed_at parsed
        assert is_takeable(info, now=T0 + timedelta(seconds=5), hostname=HOST, pid_is_alive=_alive) is False
