"""Tests for the launchd plist and the status report. Nothing here touches the machine."""

from __future__ import annotations

import json
import os
import plistlib
import subprocess
import sys
from collections.abc import Callable
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from etfp import ops, recorder
from etfp.rawstore import RawStore
from etfp.timeutil import IST, UTC

ROOT = Path("/Users/someone/ETF_Premiums")
UV = Path("/Users/someone/.local/bin/uv")


def parsed(**kwargs) -> dict:
    text = ops.render_inav_plist(project_root=ROOT, uv_path=UV, **kwargs)
    return plistlib.loads(text.encode("utf-8"))


def test_plist_is_valid_xml_with_five_weekday_entries() -> None:
    text = ops.render_inav_plist(project_root=ROOT, uv_path=UV)
    assert text.startswith("<?xml")
    plist = plistlib.loads(text.encode("utf-8"))
    assert plist["Label"] == "com.etfp.inav"
    assert plist["StartCalendarInterval"] == [
        {"Weekday": d, "Hour": 9, "Minute": 10} for d in (1, 2, 3, 4, 5)
    ]


def test_plist_program_arguments_are_exact() -> None:
    assert parsed()["ProgramArguments"] == [
        "/usr/bin/caffeinate",
        "-i",
        str(UV),
        "run",
        "--project",
        str(ROOT),
        "etfp",
        "collect",
        "inav",
        "--loop",
    ]


def test_plist_environment_paths_and_flags() -> None:
    plist = parsed()
    assert plist["WorkingDirectory"] == str(ROOT)
    assert plist["StandardOutPath"] == str(ROOT / "logs" / "launchd.inav.out.log")
    assert plist["StandardErrorPath"] == str(ROOT / "logs" / "launchd.inav.err.log")
    env = plist["EnvironmentVariables"]
    assert env["ETFP_ROOT"] == str(ROOT)
    dirs = env["PATH"].split(":")
    assert dirs[:4] == ["/usr/local/bin", "/opt/homebrew/bin", "/usr/bin", "/bin"]
    assert str(UV.parent) in dirs
    assert plist["RunAtLoad"] is False
    assert plist["ProcessType"] == "Background"


def test_plist_does_not_duplicate_uv_dir_and_accepts_label() -> None:
    plist = plistlib.loads(
        ops.render_inav_plist(
            project_root=ROOT, uv_path=Path("/opt/homebrew/bin/uv"), label="x.y"
        ).encode()
    )
    assert plist["Label"] == "x.y"
    assert plist["EnvironmentVariables"]["PATH"].split(":").count("/opt/homebrew/bin") == 1


def test_find_uv(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ops.shutil, "which", lambda name: "/somewhere/uv")
    assert ops.find_uv() == Path("/somewhere/uv")
    monkeypatch.setattr(ops.shutil, "which", lambda name: None)
    with pytest.raises(FileNotFoundError, match="uv"):
        ops.find_uv()


# -- status --------------------------------------------------------------

NOW = datetime(2026, 11, 11, 9, 20, tzinfo=IST)


def test_status_with_nothing_recorded(tmp_path: Path) -> None:
    lines = ops.status_lines(
        store=RawStore(tmp_path / "raw"), state_dir=tmp_path / "state", now=NOW
    )
    assert lines[0] == "no ticks recorded yet"
    assert "failures logged today (2026-11-11): 0" in lines
    assert "raw files today (2026-11-11): none" in lines


def test_status_reports_heartbeat_failures_and_raw_counts(tmp_path: Path) -> None:
    state = tmp_path / "state"
    store = RawStore(tmp_path / "raw")
    for hhmmss in ("091500", "093000"):
        store.save(
            source="amc_inav_mirae",
            day=date(2026, 11, 11),
            name=f"inav_{hhmmss}.json",
            content=hhmmss.encode(),
            url="https://x.test",
            status_code=200,
            fetched_at=NOW,
        )
    store.save(
        source="amc_inav_mirae",
        day=date(2026, 11, 10),
        name="inav_150000.json",
        content=b"yesterday",
        url="https://x.test",
        status_code=200,
        fetched_at=NOW,
    )
    heartbeat = {
        "last_tick_started": "2026-11-11T09:15:02+05:30",
        "last_tick_finished": "2026-11-11T09:15:09+05:30",
        "scheduled_for": "09:15",
        "ok": 1,
        "failed": 1,
        "sources": {
            "amc_inav_mirae": {"ok": True, "last_ok_at": "2026-11-11T09:15:02+05:30"},
            "nse_etf_api": {
                "ok": False,
                "error_type": "SourceBlockedError",
                "error": "blocked (HTTP 403)",
                "last_ok_at": None,
            },
        },
    }
    recorder.write_atomic(state / "inav_heartbeat.json", heartbeat)
    recorder.append_failure(
        state, ts=NOW, source="nse_etf_api", error_type="SourceBlockedError", error="x",
        scheduled_for=None,
    )  # fmt: skip
    text = "\n".join(ops.status_lines(store=store, state_dir=state, now=NOW))
    assert "4 min ago" in text
    assert "scheduled slot:     09:15" in text
    assert "sources: 1 ok, 1 failed" in text
    assert "OK     amc_inav_mirae" in text
    assert "FAILED nse_etf_api" in text
    assert "SourceBlockedError: blocked (HTTP 403)" in text
    assert "failures logged today (2026-11-11): 1" in text
    assert "amc_inav_mirae: 2" in text  # yesterday's file is not counted


# ===========================================================================
# Daily plists
# ===========================================================================
def daily_plist(phase: str) -> dict:
    text = ops.render_daily_plist(project_root=ROOT, uv_path=UV, phase=phase)  # type: ignore[arg-type]
    return plistlib.loads(text.encode("utf-8"))


@pytest.mark.parametrize(("phase", "hour", "minute"), [("evening", 19, 45), ("morning", 10, 35)])
def test_daily_plist_schedule_and_command(phase: str, hour: int, minute: int) -> None:
    plist = daily_plist(phase)
    assert plist["Label"] == f"com.etfp.daily-{phase}"
    assert plist["StartCalendarInterval"] == [
        {"Weekday": d, "Hour": hour, "Minute": minute} for d in (1, 2, 3, 4, 5)
    ]
    assert plist["ProgramArguments"] == [
        "/usr/bin/caffeinate",
        "-i",
        str(UV),
        "run",
        "--project",
        str(ROOT),
        "etfp",
        "run",
        "daily",
        "--phase",
        phase,
    ]
    assert plist["StandardOutPath"] == str(ROOT / "logs" / f"launchd.daily-{phase}.out.log")
    assert plist["StandardErrorPath"] == str(ROOT / "logs" / f"launchd.daily-{phase}.err.log")
    assert plist["EnvironmentVariables"]["ETFP_ROOT"] == str(ROOT)
    assert plist["RunAtLoad"] is False
    # Not "Background": macOS throttles those ~5x, which made the build take 29 minutes.
    assert plist["ProcessType"] == "Standard"


def test_render_plist_picks_each_job_and_rejects_unknown() -> None:
    labels = {
        job: plistlib.loads(ops.render_plist(job, project_root=ROOT, uv_path=UV).encode())["Label"]
        for job in ops.JOBS
    }
    assert labels == {
        "inav": "com.etfp.inav",
        "daily-evening": "com.etfp.daily-evening",
        "daily-morning": "com.etfp.daily-morning",
    }
    # the recorder output is exactly what render_inav_plist always produced
    assert ops.render_plist("inav", project_root=ROOT, uv_path=UV) == ops.render_inav_plist(
        project_root=ROOT, uv_path=UV
    )
    with pytest.raises(ValueError, match="unknown job"):
        ops.render_plist("nightly", project_root=ROOT, uv_path=UV)
    with pytest.raises(ValueError, match="phase"):
        ops.render_daily_plist(project_root=ROOT, uv_path=UV, phase="noon")  # type: ignore[arg-type]


# ===========================================================================
# Which day does a run process?
# ===========================================================================
def at(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=IST)


def test_default_phase_switches_at_18_ist() -> None:
    assert ops.default_phase(at(date(2026, 11, 13), 17, 59)) == "morning"
    assert ops.default_phase(at(date(2026, 11, 13), 18, 0)) == "evening"
    # a UTC timestamp is converted: 13:00 UTC is 18:30 in India
    utc_evening = datetime(2026, 11, 13, 13, 0, tzinfo=UTC)
    assert ops.default_phase(utc_evening) == "evening"


def test_evening_processes_today() -> None:
    assert ops.resolve_run_day("evening", None, at(date(2026, 11, 13), 19, 45)) == (
        date(2026, 11, 13),
        None,
    )


def test_evening_on_a_weekend_is_a_message_not_an_error() -> None:
    day, message = ops.resolve_run_day("evening", None, at(date(2026, 11, 14), 19, 45))
    assert day == date(2026, 11, 14)
    assert message is not None
    assert "closed" in message
    assert "Saturday" in message


def test_evening_on_a_weekday_holiday_is_a_message() -> None:
    _, message = ops.resolve_run_day("evening", None, at(date(2026, 11, 10), 19, 45))
    assert message is not None


def test_muhurat_sunday_is_a_session_so_the_run_goes_ahead() -> None:
    # 2026-11-08 is a Sunday and an NSE session (D24)
    assert ops.resolve_run_day("evening", date(2026, 11, 8), at(date(2026, 11, 8), 20)) == (
        date(2026, 11, 8),
        None,
    )


def test_monday_morning_processes_friday() -> None:
    # Friday's US NAV is published Monday morning
    assert ops.resolve_run_day("morning", None, at(date(2026, 11, 16), 10, 35)) == (
        date(2026, 11, 13),
        None,
    )


def test_morning_after_muhurat_sunday_processes_the_sunday_session() -> None:
    assert ops.resolve_run_day("morning", None, at(date(2026, 11, 9), 10, 35)) == (
        date(2026, 11, 8),
        None,
    )


def test_morning_after_a_weekday_holiday_processes_the_session_before_it() -> None:
    # 2026-11-10 is an NSE holiday: on Wed 11 Nov the last session before today is Mon 9 Nov
    assert ops.resolve_run_day("morning", None, at(date(2026, 11, 11), 10, 35))[0] == date(
        2026, 11, 9
    )


def test_morning_still_runs_on_a_holiday_morning() -> None:
    # Tue 10 Nov is a holiday but Monday's NAV still appears: the previous session is processed
    day, message = ops.resolve_run_day("morning", None, at(date(2026, 11, 10), 10, 35))
    assert (day, message) == (date(2026, 11, 9), None)


def test_explicit_date_wins_and_future_date_is_rejected() -> None:
    now = at(date(2026, 11, 13), 20)
    assert ops.resolve_run_day("evening", date(2026, 11, 12), now) == (date(2026, 11, 12), None)
    with pytest.raises(ValueError, match="future"):
        ops.resolve_run_day("evening", date(2026, 11, 14), now)
    # an explicit non-session date is still just a message
    saturday = date(2026, 11, 14)
    assert ops.resolve_run_day("morning", saturday, at(date(2026, 11, 16), 10, 35))[1] is not None


# ===========================================================================
# Lock
# ===========================================================================
LOCK_NOW = at(date(2026, 11, 13), 19, 45)
DAY = date(2026, 11, 13)


def hold(state: Path, **kwargs):
    return ops.run_lock(state, now=LOCK_NOW, phase="evening", day=DAY, **kwargs)


def test_lock_blocks_a_second_run_and_is_released_afterwards(tmp_path: Path) -> None:
    with hold(tmp_path):
        assert (tmp_path / ops.LOCK_NAME).exists()
        with (
            pytest.raises(ops.RunLockedError, match="another `etfp run daily` is running"),
            hold(tmp_path),
        ):
            pytest.fail("must not get the lock")
        # the failed attempt must not have removed the holder's lock
        assert (tmp_path / ops.LOCK_NAME).exists()
    assert not (tmp_path / ops.LOCK_NAME).exists()
    with hold(tmp_path) as lock:  # free again
        assert lock.taken_over is None


def test_lock_names_the_holder(tmp_path: Path) -> None:
    with (
        hold(tmp_path, pid=os.getpid()),
        pytest.raises(ops.RunLockedError) as caught,
        hold(tmp_path),
    ):
        pass
    assert f"pid {os.getpid()}" in str(caught.value)
    assert "evening 2026-11-13" in str(caught.value)


def write_lock(state: Path, *, started: datetime, pid: int = 1234) -> None:
    (state / ops.LOCK_NAME).write_text(
        json.dumps(
            {
                "pid": pid,
                "started_at": started.isoformat(),
                "phase": "morning",
                "day": "2026-11-12",
                "token": "old",
            }
        )
    )


def test_lock_just_under_two_hours_old_is_respected(tmp_path: Path) -> None:
    write_lock(tmp_path, started=LOCK_NOW - timedelta(hours=1, minutes=59))
    with pytest.raises(ops.RunLockedError), hold(tmp_path, pid_alive=lambda pid: True):
        pass


def test_lock_two_hours_old_is_reported_and_taken_over(tmp_path: Path) -> None:
    write_lock(tmp_path, started=LOCK_NOW - timedelta(hours=2))
    with hold(tmp_path, pid_alive=lambda pid: True) as lock:  # process "alive" but hung
        assert lock.taken_over is not None
        assert "stale lock" in lock.taken_over
        assert "2.0 h old" in lock.taken_over
        mine = json.loads((tmp_path / ops.LOCK_NAME).read_text())
        assert mine["token"] != "old"
        assert mine["phase"] == "evening"
    assert not (tmp_path / ops.LOCK_NAME).exists()


def test_lock_of_a_dead_process_is_taken_over_at_once(tmp_path: Path) -> None:
    write_lock(tmp_path, started=LOCK_NOW - timedelta(minutes=5), pid=4242)
    seen: list[int] = []

    def alive(pid: int) -> bool:
        seen.append(pid)
        return False

    with hold(tmp_path, pid_alive=alive) as lock:
        assert seen == [4242]
        assert lock.taken_over is not None
        assert "no longer exists" in lock.taken_over


def test_unreadable_lock_is_judged_by_file_age(tmp_path: Path) -> None:
    lock = tmp_path / ops.LOCK_NAME
    lock.write_text("not json")
    now_stamp = LOCK_NOW.timestamp()
    os.utime(lock, (now_stamp - 60, now_stamp - 60))  # a minute old: respect it
    with pytest.raises(ops.RunLockedError), hold(tmp_path):
        pass
    os.utime(lock, (now_stamp - 3 * 3600, now_stamp - 3 * 3600))  # three hours old: replace it
    with hold(tmp_path) as result:
        assert result.taken_over is not None


def test_a_run_never_deletes_a_lock_that_is_not_its_own(tmp_path: Path) -> None:
    with hold(tmp_path):
        write_lock(tmp_path, started=LOCK_NOW)  # someone took over while we were running
    assert (tmp_path / ops.LOCK_NAME).exists()
    assert json.loads((tmp_path / ops.LOCK_NAME).read_text())["token"] == "old"


def test_real_pid_check_knows_this_process_and_a_dead_one() -> None:
    assert ops._pid_alive(os.getpid()) is True
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait()
    assert ops._pid_alive(child.pid) is False


# ===========================================================================
# run_daily: order, failures, record
# ===========================================================================
class Clock:
    """Each call moves 1.5 s on, so every step lasts exactly 1.5 s."""

    def __init__(self) -> None:
        self.now = LOCK_NOW

    def __call__(self) -> datetime:
        current = self.now
        self.now += timedelta(seconds=1.5)
        return current


class Fakes:
    """Fake steps that remember the order they ran in."""

    def __init__(self, **status: str) -> None:
        self.calls: list[str] = []
        self.status = {"collect": "ok", "build": "ok", "export": "ok", "alerts": "ok", **status}
        self.raises: dict[str, BaseException] = {}
        self.during: dict[str, Callable[[], None]] = {}
        self.collect_args: list[tuple[date, str]] = []

    def _step(self, name: str) -> ops.StepReport:
        self.calls.append(name)
        if name in self.during:
            self.during[name]()
        if name in self.raises:
            raise self.raises[name]
        return ops.StepReport(self.status[name], f"{name} says {self.status[name]}")  # type: ignore[arg-type]

    def steps(self) -> ops.DailySteps:
        def collect(day: date, phase: str) -> ops.StepReport:
            self.collect_args.append((day, phase))
            return self._step("collect")

        return ops.DailySteps(
            collect=collect,  # type: ignore[arg-type]
            build=lambda: self._step("build"),
            export=lambda: self._step("export"),
            alerts=lambda: self._step("alerts"),
        )


def go(
    state: Path, fakes: Fakes, *, phase: str = "evening", **kwargs
) -> tuple[ops.RunResult, list[str], list[str]]:
    out: list[str] = []
    err: list[str] = []
    result = ops.run_daily(
        day=DAY,
        phase=phase,  # type: ignore[arg-type]
        steps=fakes.steps(),
        state_dir=state,
        clock=kwargs.pop("clock", Clock()),
        out=out.append,
        err=err.append,
        **kwargs,
    )
    return result, out, err


def test_steps_run_in_order_and_exit_0(tmp_path: Path) -> None:
    fakes = Fakes()
    result, out, err = go(tmp_path, fakes)
    assert fakes.calls == ["collect", "build", "export", "alerts"]
    assert fakes.collect_args == [(DAY, "evening")]
    assert result.exit_code == 0
    assert err == []
    assert out[-1].startswith("run daily evening 2026-11-13: OK")


def test_morning_phase_is_passed_to_collect(tmp_path: Path) -> None:
    fakes = Fakes()
    go(tmp_path, fakes, phase="morning")
    assert fakes.collect_args == [(DAY, "morning")]


def test_failed_collect_does_not_block_the_rest_but_fails_the_run(tmp_path: Path) -> None:
    fakes = Fakes(collect="failed")
    result, _, err = go(tmp_path, fakes)
    assert fakes.calls == ["collect", "build", "export", "alerts"]
    assert result.exit_code == 1
    assert any(line.startswith("[collect] FAILED") for line in err)
    assert "FAILED steps: collect" in err[-1]
    assert result.record["outcome"] == "failed"


def test_failed_build_skips_export_but_alerts_still_run(tmp_path: Path) -> None:
    fakes = Fakes(build="failed")
    result, out, err = go(tmp_path, fakes)
    assert fakes.calls == ["collect", "build", "alerts"]
    statuses = {s["name"]: s["status"] for s in result.record["steps"]}
    assert statuses == {
        "collect": "ok",
        "build": "failed",
        "export": "skipped",
        "alerts": "ok",
    }
    assert result.exit_code == 1
    assert "[build] FAILED" in "\n".join(err)
    assert any(line.startswith("[export] SKIPPED") for line in out)  # skipped is not loud


def test_a_step_that_raises_is_a_failed_step_with_the_error_named(tmp_path: Path) -> None:
    fakes = Fakes()
    fakes.raises["build"] = KeyError("etf_day")
    result, _, err = go(tmp_path, fakes)
    assert result.exit_code == 1
    build = next(s for s in result.record["steps"] if s["name"] == "build")
    assert build["status"] == "failed"
    assert build["detail"] == "KeyError: 'etf_day'"
    assert fakes.calls == ["collect", "build", "alerts"]
    assert any("KeyError" in line for line in err)


def test_warning_does_not_fail_the_run_but_is_loud(tmp_path: Path) -> None:
    fakes = Fakes(alerts="warning")
    result, out, err = go(tmp_path, fakes)
    assert result.exit_code == 0
    assert result.record["outcome"] == "ok"
    assert any(line.startswith("[alerts] WARNING") for line in err)
    assert out[-1].startswith("run daily evening 2026-11-13: OK")


def test_no_alerts_skips_the_alerts_step(tmp_path: Path) -> None:
    fakes = Fakes()
    result, _, _ = go(tmp_path, fakes, no_alerts=True)
    assert fakes.calls == ["collect", "build", "export"]
    alerts = next(s for s in result.record["steps"] if s["name"] == "alerts")
    assert alerts["status"] == "skipped"
    assert "--no-alerts" in alerts["detail"]
    assert result.exit_code == 0


def test_run_record_content_and_location(tmp_path: Path) -> None:
    fakes = Fakes(collect="failed")
    result, _, _ = go(tmp_path, fakes)
    path = tmp_path / "runs" / "2026-11-13_evening.json"
    assert result.record_path == path
    saved = json.loads(path.read_text())
    assert saved == result.record
    assert saved["phase"] == "evening"
    assert saved["day"] == "2026-11-13"
    assert saved["outcome"] == "failed"
    assert saved["exit_code"] == 1
    assert saved["started_at"] == "2026-11-13T19:45:00+05:30"
    assert [s["name"] for s in saved["steps"]] == ["collect", "build", "export", "alerts"]
    assert all(s["duration_s"] == 1.5 for s in saved["steps"])
    assert saved["steps"][0]["detail"] == "collect says failed"
    assert saved["steps"][0]["started_at"].endswith("+05:30")
    assert saved["finished_at"].endswith("+05:30")
    # 1 call for the start, 2 per step x 4 steps, 1 for the end: 10 calls, 9 gaps of 1.5 s
    assert saved["duration_s"] == 13.5


def test_step_data_is_kept_in_the_record(tmp_path: Path) -> None:
    steps = ops.DailySteps(
        collect=lambda d, p: ops.StepReport("failed", "x", {"failed": [{"source": "yahoo"}]}),
        build=lambda: ops.StepReport("ok"),
        export=lambda: ops.StepReport("ok"),
        alerts=lambda: ops.StepReport("ok"),
    )
    result = ops.run_daily(
        day=DAY, phase="evening", steps=steps, state_dir=tmp_path, clock=Clock(),
        out=lambda t: None, err=lambda t: None,
    )  # fmt: skip
    assert result.record["steps"][0]["data"] == {"failed": [{"source": "yahoo"}]}
    assert "data" not in result.record["steps"][1]


def test_record_is_marked_running_while_a_step_executes(tmp_path: Path) -> None:
    fakes = Fakes()
    seen: list[dict] = []
    path = tmp_path / "runs" / "2026-11-13_evening.json"
    fakes.during["build"] = lambda: seen.append(json.loads(path.read_text()))
    go(tmp_path, fakes)
    assert seen[0]["outcome"] == "running"
    assert seen[0]["exit_code"] is None
    assert [s["name"] for s in seen[0]["steps"]] == ["collect"]  # earlier steps already saved


def test_interrupt_leaves_an_interrupted_record_and_frees_the_lock(tmp_path: Path) -> None:
    fakes = Fakes()
    fakes.raises["build"] = KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        go(tmp_path, fakes)
    saved = json.loads((tmp_path / "runs" / "2026-11-13_evening.json").read_text())
    assert saved["outcome"] == "interrupted"
    assert [s["name"] for s in saved["steps"]] == ["collect"]
    assert not (tmp_path / ops.LOCK_NAME).exists()


def test_overlapping_run_runs_nothing_and_leaves_the_first_record_alone(tmp_path: Path) -> None:
    inner = Fakes()
    outer = Fakes()
    seen: list[str] = []

    def second_run_while_first_is_building() -> None:
        with pytest.raises(ops.RunLockedError):
            go(tmp_path, inner)
        seen.append("tried")

    outer.during["build"] = second_run_while_first_is_building
    result, _, _ = go(tmp_path, outer)
    assert seen == ["tried"]
    assert inner.calls == []  # no step of the blocked run executed
    assert result.exit_code == 0
    assert result.record["outcome"] == "ok"
    assert not (tmp_path / ops.LOCK_NAME).exists()


def test_stale_lock_is_taken_over_and_recorded(tmp_path: Path) -> None:
    write_lock(tmp_path, started=LOCK_NOW - timedelta(hours=3))
    fakes = Fakes()
    result, _, err = go(tmp_path, fakes, pid_alive=lambda pid: True)
    assert fakes.calls == ["collect", "build", "export", "alerts"]
    assert "stale lock taken over" in result.record["lock_taken_over"]
    assert err[0].startswith("WARNING: stale lock taken over")
    assert result.exit_code == 0  # a taken-over lock is reported, not a failure


def test_a_rerun_overwrites_the_record_of_the_same_day_and_phase(tmp_path: Path) -> None:
    go(tmp_path, Fakes(collect="failed"))
    result, _, _ = go(tmp_path, Fakes())
    saved = json.loads(result.record_path.read_text())
    assert saved["outcome"] == "ok"
    assert len(list((tmp_path / "runs").glob("*.json"))) == 1


# ===========================================================================
# etfp status: last run of each phase
# ===========================================================================
STATUS_NOW = at(date(2026, 11, 13), 20, 0)


def status_text(tmp_path: Path) -> str:
    return "\n".join(
        ops.status_lines(
            store=RawStore(tmp_path / "raw"), state_dir=tmp_path / "state", now=STATUS_NOW
        )
    )


def test_status_without_runs_says_never(tmp_path: Path) -> None:
    text = status_text(tmp_path)
    assert "last evening run: never" in text
    assert "last morning run: never" in text


def test_status_shows_the_last_run_of_each_phase_with_steps(tmp_path: Path) -> None:
    state = tmp_path / "state"
    fakes = Fakes(build="failed")
    go(state, fakes, phase="evening")
    go(state, Fakes(alerts="warning"), phase="morning", clock=Clock())
    text = status_text(tmp_path)
    assert "last evening run: 2026-11-13" in text
    assert "FAILED" in text.split("last morning run")[0]
    assert "build    FAILED   1.5 s  build says failed" in text
    assert "export   SKIPPED" in text
    morning = text.split("last morning run")[1]
    assert "OK (1 warning(s))" in morning


def write_run(state: Path, day: str, phase: str, started: str, outcome: str = "ok") -> None:
    runs = state / "runs"
    runs.mkdir(parents=True, exist_ok=True)
    record = {"phase": phase, "day": day, "started_at": started, "outcome": outcome, "steps": []}
    (runs / f"{day}_{phase}.json").write_text(json.dumps(record))


def test_status_picks_the_latest_started_run_not_the_latest_date_in_the_name(
    tmp_path: Path,
) -> None:
    state = tmp_path / "state"
    write_run(state, "2026-11-11", "morning", "2026-11-12T10:35:00+05:30")
    write_run(state, "2026-11-12", "morning", "2026-11-12T10:40:00+05:30")
    (state / "runs" / "2026-11-13_morning.json").write_text("{broken")  # ignored
    assert "last morning run: 2026-11-12" in status_text(tmp_path)


def test_status_flags_a_run_stuck_in_running(tmp_path: Path) -> None:
    state = tmp_path / "state"
    write_run(state, "2026-11-13", "evening", "2026-11-13T19:45:00+05:30", "running")
    assert "RUNNING" in status_text(tmp_path)
    write_run(state, "2026-11-13", "evening", "2026-11-13T15:00:00+05:30", "running")
    assert "INTERRUPTED" in status_text(tmp_path)
