"""Tests for the tick loop. No network, no real sleeping: fake sources and a fake clock."""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, time, timedelta
from pathlib import Path

import httpx
import pytest

from etfp import recorder
from etfp.config import load_collect_settings
from etfp.http import PoliteClient, SourceBlockedError
from etfp.rawstore import RawRecord, RawStore
from etfp.recorder import (
    TickResult,
    read_heartbeat,
    run_loop,
    run_tick,
    single_instance_lock,
    write_atomic,
)
from etfp.timeutil import IST

SESSION_DAY = date(2026, 11, 11)  # a Wednesday, normal trading day
HOLIDAY = date(2026, 11, 10)  # NSE holiday


def at(hh: int, mm: int, ss: int = 0, day: date = SESSION_DAY) -> datetime:
    return datetime(day.year, day.month, day.day, hh, mm, ss, tzinfo=IST)


class FakeClock:
    """A clock that only moves when someone sleeps. Optionally jumps once (laptop lid closed)."""

    def __init__(
        self, start: datetime, jump_after: datetime | None = None, jump_to: datetime | None = None
    ) -> None:
        self.t = start
        self.sleeps: list[float] = []
        self._jump_after = jump_after
        self._jump_to = jump_to

    def now(self) -> datetime:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.t += timedelta(seconds=seconds)
        if self._jump_after is not None and self.t >= self._jump_after:
            assert self._jump_to is not None
            self.t = self._jump_to
            self._jump_after = None


def fake_record(source: str, size: int = 100, *, duplicate: bool = False) -> RawRecord:
    name = "inav_000000.json"
    return RawRecord(
        source=source,
        day=SESSION_DAY,
        name=name,
        path=Path("/nonexistent") / source / name,
        relpath=f"{source}/{SESSION_DAY.isoformat()}/{name}",
        sha256="0" * 64,
        size_bytes=size,
        url="https://example.test",
        status_code=200,
        fetched_at=at(9, 15),
        version=1,
        duplicate=duplicate,
    )


class FakeSources:
    """Source registry whose behaviour can be changed between ticks."""

    def __init__(self, names: list[str]) -> None:
        self.calls: list[str] = []
        self.fail: dict[str, BaseException] = {}
        self.names = names

    def registry(self) -> dict:
        def make(name: str):
            def fn(client, store):
                self.calls.append(name)
                if name in self.fail:
                    raise self.fail[name]
                return fake_record(name)

            return fn

        return {n: make(n) for n in self.names}


@pytest.fixture
def client() -> PoliteClient:
    def no_network(request: httpx.Request) -> httpx.Response:
        raise AssertionError("the recorder tests must not make requests")

    return PoliteClient(transport=httpx.MockTransport(no_network))


@pytest.fixture
def store(tmp_path: Path) -> RawStore:
    return RawStore(tmp_path / "raw")


@pytest.fixture
def state(tmp_path: Path) -> Path:
    return tmp_path / "state"


def failure_lines(state: Path) -> list[dict]:
    path = state / "failures.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


# -- run_tick ------------------------------------------------------------


def test_all_sources_ok_writes_heartbeat(client, store, state) -> None:
    fake = FakeSources(["a", "b", "c"])
    clock = FakeClock(at(9, 15, 2))
    result = run_tick(
        client,
        store,
        sources=fake.registry(),
        scheduled_for=time(9, 15),
        state_dir=state,
        now=clock.now,
    )
    assert fake.calls == ["a", "b", "c"]  # in registry order
    assert [o.ok for o in result.outcomes] == [True, True, True]
    assert result.scheduled_for == time(9, 15)

    beat = read_heartbeat(state)
    assert beat is not None
    assert beat["ok"] == 3
    assert beat["failed"] == 0
    assert beat["scheduled_for"] == "09:15"
    assert beat["last_tick_started"] == "2026-11-11T09:15:02+05:30"
    assert beat["last_tick_finished"] == "2026-11-11T09:15:02+05:30"
    assert beat["sources"]["a"]["ok"] is True
    assert beat["sources"]["a"]["relpath"] == "a/2026-11-11/inav_000000.json"
    assert beat["sources"]["a"]["last_ok_at"] == "2026-11-11T09:15:02+05:30"
    assert beat["sources"]["a"]["error_type"] is None
    assert failure_lines(state) == []


def test_one_source_failing_never_stops_the_others(client, store, state, caplog) -> None:
    fake = FakeSources(["a", "b", "c", "d"])
    clock = FakeClock(at(9, 15))
    run_tick(
        client,
        store,
        sources=fake.registry(),
        scheduled_for=time(9, 15),
        state_dir=state,
        now=clock.now,
    )

    clock.t = at(9, 30)
    fake.fail["b"] = SourceBlockedError(
        "b for 2026-11-11 blocked (HTTP 403): https://x.test",
        source="b",
        day=SESSION_DAY,
        url="https://x.test",
        status_code=403,
    )
    fake.fail["c"] = KeyError("surprise")
    fake.calls.clear()
    with caplog.at_level(logging.ERROR, logger="etfp.recorder"):
        result = run_tick(
            client,
            store,
            sources=fake.registry(),
            scheduled_for=time(9, 30),
            state_dir=state,
            now=clock.now,
        )

    assert fake.calls == ["a", "b", "c", "d"]  # d still ran after two failures
    assert [o.ok for o in result.outcomes] == [True, False, False, True]
    blocked, odd = result.outcomes[1], result.outcomes[2]
    assert blocked.error_type == "SourceBlockedError"
    assert "HTTP 403" in (blocked.error or "")
    assert odd.error_type == "KeyError"
    assert blocked.relpath is None

    lines = failure_lines(state)
    assert len(lines) == 2
    assert [(ln["source"], ln["error_type"]) for ln in lines] == [
        ("b", "SourceBlockedError"),
        ("c", "KeyError"),
    ]
    assert all(ln["scheduled_for"] == "09:30" for ln in lines)
    assert lines[0]["ts"].endswith("+05:30")

    beat = read_heartbeat(state)
    assert beat is not None
    assert (beat["ok"], beat["failed"]) == (2, 2)
    assert beat["sources"]["b"]["ok"] is False
    assert beat["sources"]["b"]["error_type"] == "SourceBlockedError"
    # last_ok_at is carried over from the 09:15 tick for the failed sources
    assert beat["sources"]["b"]["last_ok_at"] == "2026-11-11T09:15:00+05:30"
    assert beat["sources"]["c"]["last_ok_at"] == "2026-11-11T09:15:00+05:30"
    assert beat["sources"]["a"]["last_ok_at"] == "2026-11-11T09:30:00+05:30"
    # each failed source was logged at ERROR with its name
    errors = [r.getMessage() for r in caplog.records if r.levelname == "ERROR"]
    assert any("source b failed" in m for m in errors)
    assert any("source c failed" in m for m in errors)


def test_failed_source_that_never_worked_has_no_last_ok(client, store, state) -> None:
    fake = FakeSources(["a"])
    fake.fail["a"] = ValueError("boom")
    run_tick(client, store, sources=fake.registry(), state_dir=state, now=FakeClock(at(9, 15)).now)
    beat = read_heartbeat(state)
    assert beat is not None
    assert beat["scheduled_for"] is None
    assert beat["sources"]["a"]["last_ok_at"] is None


@pytest.mark.parametrize("exc", [KeyboardInterrupt(), SystemExit(1)])
def test_keyboard_interrupt_and_system_exit_are_not_swallowed(client, store, state, exc) -> None:
    fake = FakeSources(["a", "b"])
    fake.fail["a"] = exc
    with pytest.raises(type(exc)):
        run_tick(
            client, store, sources=fake.registry(), state_dir=state, now=FakeClock(at(9, 15)).now
        )
    assert fake.calls == ["a"]


def test_duplicate_flag_and_bytes_in_log(client, store, state, caplog) -> None:
    def dup(c, s):
        return fake_record("dup", 50, duplicate=True)

    def fresh(c, s):
        return fake_record("fresh", 1234)

    with caplog.at_level(logging.INFO, logger="etfp.recorder"):
        result = run_tick(
            client, store, sources={"dup": dup, "fresh": fresh}, state_dir=state,
            now=FakeClock(at(9, 15)).now,
        )  # fmt: skip
    assert [o.duplicate for o in result.outcomes] == [True, False]
    info = [r.getMessage() for r in caplog.records if r.levelname == "INFO"]
    assert any("2 ok, 0 failed, 1234 bytes saved" in m for m in info)


def test_state_file_trouble_does_not_crash_the_tick(client, store, state, monkeypatch) -> None:
    def broken(path, payload):
        raise OSError("disk full")

    monkeypatch.setattr(recorder, "write_atomic", broken)
    fake = FakeSources(["a"])
    result = run_tick(
        client, store, sources=fake.registry(), state_dir=state, now=FakeClock(at(9, 15)).now
    )
    assert result.ok_count == 1


def test_default_registry_order_and_names() -> None:
    assert list(recorder.default_sources()) == [
        "nse_etf_api",
        "amc_inav_motilal",
        "amc_inav_mirae",
        "amc_inav_nippon",
    ]


def test_missing_nse_module_is_just_a_failed_source(client, store, state, monkeypatch) -> None:
    import builtins

    real_import = builtins.__import__

    def refuse(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "etfp.collect" and fromlist and "nse_etf_api" in fromlist:
            raise ImportError("no module named nse_etf_api")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", refuse)
    outcome = recorder._run_source("nse_etf_api", recorder._nse_etf_api, client, store)
    assert outcome.ok is False
    assert outcome.error_type == "ImportError"


# -- heartbeat file ------------------------------------------------------


def test_read_heartbeat_missing_and_corrupt(state) -> None:
    assert read_heartbeat(state) is None
    state.mkdir(parents=True)
    (state / "inav_heartbeat.json").write_text("{not json")
    assert read_heartbeat(state) is None


def test_atomic_write_leaves_no_partial_file(tmp_path: Path) -> None:
    target = tmp_path / "state" / "inav_heartbeat.json"
    write_atomic(target, {"v": 1})
    assert json.loads(target.read_text()) == {"v": 1}

    with pytest.raises(TypeError):
        write_atomic(target, {"v": {1, 2, 3}})  # a set cannot be dumped as JSON
    assert json.loads(target.read_text()) == {"v": 1}  # old content untouched
    assert [p.name for p in target.parent.iterdir()] == ["inav_heartbeat.json"]  # no temp left

    fresh = tmp_path / "state" / "new.json"
    with pytest.raises(TypeError):
        write_atomic(fresh, object())
    assert not fresh.exists()
    assert sorted(p.name for p in target.parent.iterdir()) == ["inav_heartbeat.json"]


# -- run_loop ------------------------------------------------------------


@pytest.fixture(scope="module")
def settings():
    return load_collect_settings()


def loop(client, store, settings, state, clock, fake, **kwargs) -> list[TickResult]:
    return run_loop(
        client=client,
        store=store,
        settings=settings,
        state_dir=state,
        now=clock.now,
        sleep=clock.sleep,
        sources=fake.registry(),
        **kwargs,
    )


def test_closed_day_returns_nothing_and_calls_no_source(client, store, settings, state) -> None:
    fake = FakeSources(["a"])
    clock = FakeClock(at(9, 0, day=HOLIDAY))
    assert loop(client, store, settings, state, clock, fake) == []
    assert fake.calls == []
    assert clock.sleeps == []


def test_force_runs_on_a_closed_day(client, store, settings, state) -> None:
    fake = FakeSources(["a"])
    clock = FakeClock(at(15, 20, day=HOLIDAY))
    results = loop(client, store, settings, state, clock, fake, force=True)
    assert [r.scheduled_for for r in results] == [time(15, 15), time(15, 30), time(15, 45)]
    assert fake.calls == ["a", "a", "a"]


def test_full_day_runs_27_ticks_at_the_scheduled_times(client, store, settings, state) -> None:
    fake = FakeSources(["a", "b"])
    clock = FakeClock(at(9, 0))
    results = loop(client, store, settings, state, clock, fake)

    expected = settings.inav_tick_times()
    assert len(expected) == 27
    assert [r.scheduled_for for r in results] == expected
    assert [r.started_at for r in results] == [at(t.hour, t.minute) for t in expected]
    assert len(fake.calls) == 27 * 2
    assert clock.sleeps  # it did wait
    assert max(clock.sleeps) <= 20
    assert failure_lines(state) == []
    beat = read_heartbeat(state)
    assert beat is not None
    assert beat["scheduled_for"] == "15:45"


def test_start_mid_session_runs_the_current_slot_now_then_continues(
    client, store, settings, state
) -> None:
    fake = FakeSources(["a"])
    clock = FakeClock(at(11, 2))
    results = loop(client, store, settings, state, clock, fake)
    assert results[0].scheduled_for == time(11, 0)
    assert results[0].started_at == at(11, 2)  # immediately, no waiting
    assert results[1].scheduled_for == time(11, 15)
    assert results[1].started_at == at(11, 15)
    assert [r.scheduled_for for r in results][-1] == time(15, 45)
    assert len(results) == 20  # 11:00 ... 15:30 is 19 slots, plus the 15:45 post-close one
    assert failure_lines(state) == []  # earlier slots were before we started: not "missed"


def test_start_five_minutes_after_a_slot_still_runs_it(client, store, settings, state) -> None:
    fake = FakeSources(["a"])
    clock = FakeClock(at(11, 20))
    results = loop(client, store, settings, state, clock, fake)
    assert results[0].scheduled_for == time(11, 15)
    assert results[0].started_at == at(11, 20)
    assert results[1].scheduled_for == time(11, 30)


def test_start_just_after_the_closing_slot_runs_it_then_the_post_close_one(
    client, store, settings, state
) -> None:
    fake = FakeSources(["a"])
    clock = FakeClock(at(15, 31))  # 15:30 is 1 minute ago, so it runs now
    results = loop(client, store, settings, state, clock, fake)
    assert [r.scheduled_for for r in results] == [time(15, 30), time(15, 45)]


def test_start_after_the_last_slot_returns_nothing(client, store, settings, state) -> None:
    fake = FakeSources(["a"])
    clock = FakeClock(at(15, 50))
    assert loop(client, store, settings, state, clock, fake) == []
    assert fake.calls == []


def test_sleep_gap_records_missed_ticks_and_resumes(client, store, settings, state) -> None:
    fake = FakeSources(["a"])
    # starts at 10:00 (tick runs), then the "laptop sleeps" and wakes at 11:40
    clock = FakeClock(at(10, 0), jump_after=at(10, 1), jump_to=at(11, 40))
    results = loop(client, store, settings, state, clock, fake)

    scheduled = [r.scheduled_for for r in results]
    assert scheduled[0] == time(10, 0)
    assert scheduled[1] == time(11, 30)  # 10 minutes late: still taken
    assert results[1].started_at == at(11, 40)
    assert time(10, 15) not in scheduled
    assert time(11, 15) not in scheduled
    assert scheduled[2] == time(11, 45)

    missed = failure_lines(state)
    assert [(m["source"], m["error_type"], m["scheduled_for"]) for m in missed] == [
        ("_tick", "MissedTick", t) for t in ("10:15", "10:30", "10:45", "11:00", "11:15")
    ]
    assert missed[0]["ts"].startswith("2026-11-11T11:40")
    assert "10:15" in missed[0]["error"]
    assert max(clock.sleeps) <= 20


def test_each_scheduled_time_runs_at_most_once(client, store, settings, state) -> None:
    fake = FakeSources(["a"])
    clock = FakeClock(at(9, 0))
    results = loop(client, store, settings, state, clock, fake)
    slots = [r.scheduled_for for r in results]
    assert len(slots) == len(set(slots))


def test_second_loop_while_lock_held_raises(client, store, settings, state) -> None:
    fake = FakeSources(["a"])
    with single_instance_lock(state):
        with pytest.raises(RuntimeError, match="already running"):
            loop(client, store, settings, state, FakeClock(at(9, 0)), fake)
        assert fake.calls == []
    # lock released: a loop can run now (late start so it is quick)
    assert loop(client, store, settings, state, FakeClock(at(15, 50)), fake) == []


def test_lock_is_released_even_if_the_loop_is_interrupted(client, store, settings, state) -> None:
    fake = FakeSources(["a"])
    fake.fail["a"] = KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        loop(client, store, settings, state, FakeClock(at(11, 0)), fake)
    with single_instance_lock(state):  # would raise if still held
        pass
