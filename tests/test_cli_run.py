"""Tests for `etfp run daily` and `etfp ops launchd-plist --job ...` (typer CliRunner).

No network and no real build: the four real jobs are replaced by fakes, or (when the job
itself is under test) the things it calls are. The project root is a temp folder and "now"
is pinned.
"""

from __future__ import annotations

import contextlib
import json
import os
import plistlib
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from etfp import calendars, cli, netcheck, ops
from etfp.collect import jobs
from etfp.config import load_collect_settings
from etfp.timeutil import IST

runner = CliRunner()
SETTINGS = load_collect_settings()
REAL_HOLIDAYS = calendars.default_overrides_path()  # read before tests move the project root
EVENING = datetime(2026, 11, 13, 19, 45, tzinfo=IST)  # Friday
MONDAY_MORNING = datetime(2026, 11, 16, 10, 35, tzinfo=IST)
SATURDAY = datetime(2026, 11, 14, 19, 45, tzinfo=IST)
ONLINE = netcheck.ProbeResult(True, (netcheck.ProbeCheck("https://a.test/", True, "HTTP 200"),))
OFFLINE = netcheck.ProbeResult(
    False,
    (
        netcheck.ProbeCheck("https://a.test/", False, "DNS lookup failed (gaierror: no host)"),
        netcheck.ProbeCheck("https://b.test/", False, "DNS lookup failed (gaierror: no host)"),
    ),
)


@pytest.fixture(autouse=True)
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("ETFP_ROOT", str(tmp_path))
    # keep the real NSE holiday and Muhurat list even though the project root is a temp folder
    monkeypatch.setenv(calendars.NSE_HOLIDAYS_ENV, str(REAL_HOLIDAYS))
    monkeypatch.setattr(cli, "load_collect_settings", lambda: SETTINGS)
    monkeypatch.setattr(cli, "_configure_logging", lambda: None)
    monkeypatch.setattr(cli, "now_ist", lambda: EVENING)
    # never touch the real network: pretend the connectivity check succeeded
    monkeypatch.setattr(cli, "_probe_network", lambda: ONLINE)
    return tmp_path


def set_now(monkeypatch: pytest.MonkeyPatch, now: datetime) -> None:
    monkeypatch.setattr(cli, "now_ist", lambda: now)


class FakeJobs:
    """Replaces the real steps in `cli._real_steps` and remembers how they were called."""

    def __init__(self, **status: str) -> None:
        self.status = {"collect": "ok", "build": "ok", "export": "ok", "alerts": "ok", **status}
        self.calls: list[str] = []
        self.collect_args: list[tuple[date, str]] = []

    def step(self, name: str) -> ops.StepReport:
        self.calls.append(name)
        return ops.StepReport(self.status[name], f"{name} {self.status[name]}")  # type: ignore[arg-type]

    def steps(self) -> ops.DailySteps:
        def collect(day: date, phase: str) -> ops.StepReport:
            self.collect_args.append((day, phase))
            return self.step("collect")

        return ops.DailySteps(
            collect=collect,  # type: ignore[arg-type]
            build=lambda: self.step("build"),
            export=lambda: self.step("export"),
            alerts=lambda: self.step("alerts"),
        )


def install(monkeypatch: pytest.MonkeyPatch, fake: FakeJobs) -> FakeJobs:
    monkeypatch.setattr(cli, "_real_steps", fake.steps)
    return fake


def record_files(root: Path) -> list[str]:
    folder = root / "data" / "state" / "runs"
    return sorted(p.name for p in folder.glob("*.json")) if folder.exists() else []


# -- the command ----------------------------------------------------------------------


def test_evening_run_collects_builds_exports_alerts_and_writes_a_record(
    monkeypatch: pytest.MonkeyPatch, isolated: Path
) -> None:
    fake = install(monkeypatch, FakeJobs())
    result = runner.invoke(cli.app, ["run", "daily"])  # 19:45 -> evening by default
    assert result.exit_code == 0, result.output
    assert fake.calls == ["collect", "build", "export", "alerts"]
    assert fake.collect_args == [(date(2026, 11, 13), "evening")]
    assert record_files(isolated) == ["2026-11-13_evening.json"]
    saved = json.loads((isolated / "data/state/runs/2026-11-13_evening.json").read_text())
    assert saved["outcome"] == "ok"
    assert [s["name"] for s in saved["steps"]] == ["collect", "build", "export", "alerts"]


def test_default_phase_before_18_is_morning_and_processes_the_previous_session(
    monkeypatch: pytest.MonkeyPatch, isolated: Path
) -> None:
    set_now(monkeypatch, MONDAY_MORNING)
    fake = install(monkeypatch, FakeJobs())
    result = runner.invoke(cli.app, ["run", "daily"])
    assert result.exit_code == 0, result.output
    assert fake.collect_args == [(date(2026, 11, 13), "morning")]  # Friday
    assert record_files(isolated) == ["2026-11-13_morning.json"]


def test_explicit_date_and_phase(monkeypatch: pytest.MonkeyPatch, isolated: Path) -> None:
    fake = install(monkeypatch, FakeJobs())
    result = runner.invoke(cli.app, ["run", "daily", "--date", "2026-11-12", "--phase", "morning"])
    assert result.exit_code == 0, result.output
    assert fake.collect_args == [(date(2026, 11, 12), "morning")]
    assert record_files(isolated) == ["2026-11-12_morning.json"]


def test_non_session_day_exits_0_with_a_message_and_runs_nothing(
    monkeypatch: pytest.MonkeyPatch, isolated: Path
) -> None:
    set_now(monkeypatch, SATURDAY)
    fake = install(monkeypatch, FakeJobs())
    result = runner.invoke(cli.app, ["run", "daily", "--phase", "evening"])
    assert result.exit_code == 0
    assert "NSE was closed on 2026-11-14 (Saturday)" in result.output
    assert fake.calls == []
    assert record_files(isolated) == []


def test_muhurat_sunday_session_is_run(monkeypatch: pytest.MonkeyPatch) -> None:
    set_now(monkeypatch, datetime(2026, 11, 8, 20, 30, tzinfo=IST))  # a Sunday
    fake = install(monkeypatch, FakeJobs())
    result = runner.invoke(cli.app, ["run", "daily", "--phase", "evening"])
    assert result.exit_code == 0, result.output
    assert fake.calls == ["collect", "build", "export", "alerts"]


def test_failed_collect_still_builds_and_exit_code_is_1(
    monkeypatch: pytest.MonkeyPatch, isolated: Path
) -> None:
    fake = install(monkeypatch, FakeJobs(collect="failed"))
    result = runner.invoke(cli.app, ["run", "daily"])
    assert result.exit_code == 1
    assert fake.calls == ["collect", "build", "export", "alerts"]
    assert "[collect] FAILED" in result.stderr
    assert "FAILED steps: collect" in result.stderr


def test_failed_build_skips_export_and_exit_code_is_1(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = install(monkeypatch, FakeJobs(build="failed"))
    result = runner.invoke(cli.app, ["run", "daily"])
    assert result.exit_code == 1
    assert fake.calls == ["collect", "build", "alerts"]


def test_no_alerts_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = install(monkeypatch, FakeJobs())
    result = runner.invoke(cli.app, ["run", "daily", "--no-alerts"])
    assert result.exit_code == 0
    assert fake.calls == ["collect", "build", "export"]


@pytest.mark.parametrize(
    "args",
    [
        ["--phase", "noon"],
        ["--date", "11/13/2026"],
        ["--date", "2026-11-14"],  # tomorrow
    ],
)
def test_usage_errors_exit_2_and_run_nothing(
    args: list[str], monkeypatch: pytest.MonkeyPatch, isolated: Path
) -> None:
    fake = install(monkeypatch, FakeJobs())
    result = runner.invoke(cli.app, ["run", "daily", *args])
    assert result.exit_code == 2
    assert result.stderr.startswith("error:")
    assert fake.calls == []
    assert record_files(isolated) == []


def test_overlapping_run_exits_3_and_runs_nothing(
    monkeypatch: pytest.MonkeyPatch, isolated: Path
) -> None:
    state = isolated / "data" / "state"
    state.mkdir(parents=True)
    (state / ops.LOCK_NAME).write_text(
        json.dumps(
            {
                "pid": os.getpid(),  # a live process
                "started_at": EVENING.isoformat(),
                "phase": "evening",
                "day": "2026-11-13",
                "token": "x",
            }
        )
    )
    fake = install(monkeypatch, FakeJobs())
    result = runner.invoke(cli.app, ["run", "daily"])
    assert result.exit_code == 3
    assert "another `etfp run daily` is running" in result.stderr
    assert fake.calls == []
    assert record_files(isolated) == []
    assert (state / ops.LOCK_NAME).exists()  # not ours, not removed


def test_status_shows_the_last_run_of_each_phase(monkeypatch: pytest.MonkeyPatch) -> None:
    install(monkeypatch, FakeJobs(build="failed"))
    assert runner.invoke(cli.app, ["run", "daily"]).exit_code == 1
    result = runner.invoke(cli.app, ["status"])
    assert result.exit_code == 0
    assert "last evening run: 2026-11-13" in result.output
    assert "FAILED" in result.output
    assert "export   SKIPPED" in result.output
    assert "last morning run: never" in result.output


# -- the real jobs, with the things they call replaced ---------------------------------


class FakeClient(contextlib.AbstractContextManager):
    def __exit__(self, *exc: object) -> None:
        return None


class Recorder:
    """Stands in for `jobs.plan_daily` and `jobs.run_units`."""

    def __init__(self, fail: tuple[str, ...] = ()) -> None:
        self.plans: list[tuple[date, list[str]]] = []
        self.runs: list[dict[str, Any]] = []
        self.fail = fail
        self.catch_up_days: list[date] = []
        self.catch_up_units: list[jobs.Unit] = []
        self.network_down = False

    def plan_daily(self, day: date, sources: list[str], *, now: datetime) -> jobs.Plan:
        self.plans.append((day, list(sources)))
        units = [
            jobs.Unit(s, day, f"{s}.csv", "example.test", fetch=lambda c, st: None)  # type: ignore[arg-type,return-value]
            for s in sources
        ]
        return jobs.Plan(units, [])

    def plan_catch_up(self, day: date, store, *, now: datetime, state=None) -> jobs.Plan:
        self.catch_up_days.append(day)
        return jobs.Plan(list(self.catch_up_units), [])

    def run_units(self, units, client, store, *, force=False, on_outcome=None, **kwargs):
        units = list(units)
        self.runs.append(
            {"sources": [u.source for u in units], "force": force, "state": kwargs.get("state")}
        )
        breaker = kwargs.get("breaker")
        if self.network_down and breaker is not None:
            breaker.reason = "network down: test"
        outcomes = []
        for unit in units:
            if self.network_down:
                outcomes.append(jobs.Outcome(unit, "failed", "not tried: network down: test"))
            elif unit.source in self.fail:
                outcomes.append(jobs.Outcome(unit, "failed", "HTTP 404: not published (yet?)"))
            else:
                outcomes.append(jobs.Outcome(unit, "fetched", "ok"))
        for outcome in outcomes:
            if on_outcome:
                on_outcome(outcome)
        return outcomes


@pytest.fixture
def rec(monkeypatch: pytest.MonkeyPatch) -> Recorder:
    fake = Recorder()
    monkeypatch.setattr(jobs, "plan_daily", fake.plan_daily)
    monkeypatch.setattr(jobs, "run_units", fake.run_units)
    monkeypatch.setattr(jobs, "plan_catch_up", fake.plan_catch_up)
    monkeypatch.setattr(cli, "_build_bulk_client", lambda settings: FakeClient())
    return fake


def test_evening_collect_asks_for_every_supported_source_without_force(rec: Recorder) -> None:
    report = cli._collect_step(date(2026, 11, 13), "evening")
    assert report.status == "ok"
    assert rec.plans == [(date(2026, 11, 13), list(jobs.DAILY_SOURCES))]
    assert [r["force"] for r in rec.runs] == [False]
    assert jobs.NAVALL in rec.runs[0]["sources"]
    assert report.data["counts"]["fetched"] == len(jobs.DAILY_SOURCES)


def test_evening_collect_passes_through_sources_added_to_the_job_list(
    rec: Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(jobs, "DAILY_SOURCES", (*jobs.DAILY_SOURCES, "bse_future"))
    cli._collect_step(date(2026, 11, 13), "evening")
    assert "bse_future" in rec.plans[0][1]


def test_morning_collect_forces_only_the_amfi_window_and_leaves_snapshots_alone(
    rec: Recorder,
) -> None:
    report = cli._collect_step(date(2026, 11, 13), "morning")
    assert report.status == "ok"
    assert len(rec.runs) == 2
    first, second = rec.runs
    # pass 1 only fills holes: nothing forced, no AMFI, no "latest file" snapshots
    assert first["force"] is False
    assert jobs.AMFI_HISTORY not in first["sources"]
    assert jobs.NAVALL not in first["sources"]
    assert jobs.ETF_LIST not in first["sources"]
    assert jobs.BHAVCOPY in first["sources"]
    # pass 2: the AMFI history window is fetched again, forced, because the late NAVs are new
    assert second["sources"] == [jobs.AMFI_HISTORY]
    assert second["force"] is True


def test_collect_failure_names_the_source_and_keeps_going(
    rec: Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    rec.fail = (jobs.UDIFF,)
    report = cli._collect_step(date(2026, 11, 13), "evening")
    assert report.status == "failed"
    assert "udiff" in report.detail
    assert report.data["failed"] == [
        {
            "source": jobs.UDIFF,
            "day": "2026-11-13",
            "name": "udiff.csv",
            "detail": "HTTP 404: not published (yet?)",
        }
    ]
    assert report.data["failed"][0]["source"] == jobs.UDIFF
    # the other sources were still run in the same pass
    assert jobs.YAHOO in rec.runs[0]["sources"]


def test_a_failed_source_in_the_cli_does_not_stop_the_build_and_fails_the_run(
    rec: Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    rec.fail = (jobs.BHAVCOPY,)
    fake = FakeJobs()
    monkeypatch.setattr(
        cli,
        "_real_steps",
        lambda: ops.DailySteps(
            collect=cli._collect_step, build=lambda: fake.step("build"),
            export=lambda: fake.step("export"), alerts=lambda: fake.step("alerts"),
        ),
    )  # fmt: skip
    result = runner.invoke(cli.app, ["run", "daily"])
    assert result.exit_code == 1
    assert fake.calls == ["build", "export", "alerts"]
    assert "FAILED      bhavcopy 2026-11-13" not in result.stdout  # printed to stderr, not stdout
    assert "FAILED      bhavcopy 2026-11-13" in result.stderr


# -- offline fail-fast and catch-up --------------------------------------------------


def test_offline_probe_fails_collect_at_once_and_tries_no_download(
    rec: Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "_probe_network", lambda: OFFLINE)
    report = cli._collect_step(date(2026, 11, 13), "evening")
    assert report.status == "failed"
    assert report.detail.startswith("offline (no network)")
    assert "a.test: DNS lookup failed" in report.detail  # names what was checked
    assert report.data[ops.OFFLINE] is True
    assert len(report.data["probe"]) == 2
    assert rec.plans == [] and rec.runs == [] and rec.catch_up_days == []


def test_offline_run_skips_build_export_alerts_and_records_why(
    rec: Recorder, monkeypatch: pytest.MonkeyPatch, isolated: Path
) -> None:
    monkeypatch.setattr(cli, "_probe_network", lambda: OFFLINE)
    fake = FakeJobs()
    monkeypatch.setattr(
        cli,
        "_real_steps",
        lambda: ops.DailySteps(
            collect=cli._collect_step, build=lambda: fake.step("build"),
            export=lambda: fake.step("export"), alerts=lambda: fake.step("alerts"),
        ),
    )  # fmt: skip
    result = runner.invoke(cli.app, ["run", "daily"])
    assert result.exit_code == 1
    assert fake.calls == []  # nothing built, exported or alerted on stale data
    assert "[collect] FAILED" in result.stderr and "offline (no network)" in result.stderr
    record = json.loads(
        (isolated / "data/state/runs/2026-11-13_evening.json").read_text(encoding="utf-8")
    )
    assert record["outcome"] == "failed"
    steps = {s["name"]: s for s in record["steps"]}
    assert steps["collect"]["status"] == "failed"
    assert steps["collect"]["data"]["offline"] is True
    for name in ("build", "export", "alerts"):
        assert steps[name]["status"] == "skipped"
        assert "offline (no network)" in steps[name]["detail"]
    status = runner.invoke(cli.app, ["status"])
    assert "collect  FAILED" in status.output
    assert "offline (no network)" in status.output


def test_network_breaker_with_nothing_fetched_counts_as_offline(rec: Recorder) -> None:
    rec.network_down = True
    report = cli._collect_step(date(2026, 11, 13), "evening")
    assert report.status == "failed"
    assert "network down: test" in report.detail
    assert report.data["network_down"] == "network down: test"
    assert report.data[ops.OFFLINE] is True


def test_catch_up_runs_after_the_day_with_the_backfill_state(rec: Recorder) -> None:
    missed = date(2026, 11, 12)
    rec.catch_up_units = [
        jobs.Unit("nse_bhavcopy_full", missed, "x.csv", "example.test", fetch=lambda c, s: None)  # type: ignore[arg-type,return-value]
    ]
    report = cli._collect_step(date(2026, 11, 13), "evening")
    assert report.status == "ok"
    assert rec.catch_up_days == [date(2026, 11, 13)]
    assert len(rec.runs) == 2
    main, late = rec.runs
    assert main["state"] is None  # today's 404 stays a failure
    assert late["sources"] == ["nse_bhavcopy_full"]
    assert late["state"] is not None  # an old day's 404 is remembered, not re-asked daily
    assert report.data["catch_up_days"] == ["2026-11-12"]
    assert "catch-up of earlier sessions: 1 fetched" in report.detail


def test_morning_run_also_catches_up(rec: Recorder) -> None:
    cli._collect_step(date(2026, 11, 13), "morning")
    assert rec.catch_up_days == [date(2026, 11, 13)]


# -- the build step ---------------------------------------------------------------------


def test_build_step_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "etfp.build.build_all", lambda: {"clean": {"nav": 10, "prices": 20}, "etf_day": 5}
    )
    report = cli._build_step()
    assert report.status == "ok"
    assert "etf_day 5 rows" in report.detail


def test_build_step_clean_error_is_a_failure_with_the_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from etfp.clean.common import CleanError

    def broken() -> dict:
        raise CleanError("nav: data/raw/amfi/2026-11-13/x.txt line 4 is malformed")

    monkeypatch.setattr("etfp.build.build_all", broken)
    report = cli._build_step()
    assert report.status == "failed"
    assert "CleanError" in report.detail
    assert "line 4 is malformed" in report.detail


# -- the alerts step --------------------------------------------------------------------


class FakeAlerts:
    def __init__(self, *, no_credentials: bool = False, error: Exception | None = None) -> None:
        self.no_credentials = no_credentials
        self.error = error
        self.dry_runs: list[bool] = []

    def __call__(self, store: Any, *, dry_run: bool, **kwargs: Any) -> list[str]:
        self.dry_runs.append(dry_run)
        if self.no_credentials and not dry_run:
            from etfp.alerts import MissingCredentialsError

            raise MissingCredentialsError("missing TELEGRAM_BOT_TOKEN: set it in .env")
        if self.error is not None:
            raise self.error
        return ["a", "b"]


@pytest.fixture
def alerts_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("etfp.store.Store", lambda *a, **k: object())

    def use(fake: FakeAlerts) -> FakeAlerts:
        monkeypatch.setattr("etfp.alerts.run_alerts", fake)
        return fake

    return use


def test_alerts_step_sends_for_real_when_credentials_exist(alerts_env) -> None:
    fake = alerts_env(FakeAlerts())
    report = cli._alerts_step()
    assert (report.status, report.detail) == ("ok", "2 alert(s) sent")
    assert fake.dry_runs == [False]


def test_alerts_step_without_credentials_warns_and_previews(alerts_env) -> None:
    fake = alerts_env(FakeAlerts(no_credentials=True))
    report = cli._alerts_step()
    assert report.status == "warning"
    assert "NOT sent" in report.detail
    assert "TELEGRAM_BOT_TOKEN" in report.detail
    assert "2 alert(s) would have been sent" in report.detail
    assert fake.dry_runs == [False, True]  # the preview is a dry run, never a real send


def test_run_exits_0_when_the_only_problem_is_missing_credentials(
    alerts_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    alerts_env(FakeAlerts(no_credentials=True))
    fake = FakeJobs()
    monkeypatch.setattr(
        cli,
        "_real_steps",
        lambda: ops.DailySteps(
            collect=lambda d, p: fake.step("collect"), build=lambda: fake.step("build"),
            export=lambda: fake.step("export"), alerts=cli._alerts_step,
        ),
    )  # fmt: skip
    result = runner.invoke(cli.app, ["run", "daily"])
    assert result.exit_code == 0, result.output
    assert "[alerts] WARNING" in result.stderr
    assert "credentials missing" in result.stderr


def test_missing_credentials_does_not_hide_a_real_failure(
    alerts_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    alerts_env(FakeAlerts(no_credentials=True))
    fake = FakeJobs(build="failed")
    monkeypatch.setattr(
        cli,
        "_real_steps",
        lambda: ops.DailySteps(
            collect=lambda d, p: fake.step("collect"), build=lambda: fake.step("build"),
            export=lambda: fake.step("export"), alerts=cli._alerts_step,
        ),
    )  # fmt: skip
    assert runner.invoke(cli.app, ["run", "daily"]).exit_code == 1


def test_alerts_delivery_error_is_a_failure(alerts_env) -> None:
    from etfp.alerts import AlertDeliveryError

    alerts_env(FakeAlerts(error=AlertDeliveryError("Telegram said 400", sent=[], failed=[])))
    report = cli._alerts_step()
    assert report.status == "failed"
    assert "Telegram said 400" in report.detail


def test_alerts_already_running_is_a_failure(alerts_env) -> None:
    from etfp.alerts import AlertsAlreadyRunningError

    alerts_env(FakeAlerts(error=AlertsAlreadyRunningError("another alerts run")))
    assert cli._alerts_step().status == "failed"


# -- plists from the command line ---------------------------------------------------------


@pytest.mark.parametrize(
    ("job", "label"),
    [
        ("daily-evening", "com.etfp.daily-evening"),
        ("daily-morning", "com.etfp.daily-morning"),
        ("inav", "com.etfp.inav"),
    ],
)
def test_launchd_plist_job_option(
    job: str, label: str, monkeypatch: pytest.MonkeyPatch, isolated: Path
) -> None:
    monkeypatch.setattr(cli.ops, "find_uv", lambda: Path("/opt/homebrew/bin/uv"))
    result = runner.invoke(cli.app, ["ops", "launchd-plist", "--job", job])
    assert result.exit_code == 0
    plist = plistlib.loads(result.stdout.encode())
    assert plist["Label"] == label
    assert plist["EnvironmentVariables"]["ETFP_ROOT"] == str(isolated.resolve())


def test_launchd_plist_default_is_still_the_recorder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cli.ops, "find_uv", lambda: Path("/opt/homebrew/bin/uv"))
    default = runner.invoke(cli.app, ["ops", "launchd-plist"])
    explicit = runner.invoke(cli.app, ["ops", "launchd-plist", "--job", "inav"])
    assert default.stdout == explicit.stdout
    assert plistlib.loads(default.stdout.encode())["ProgramArguments"][-3:] == [
        "collect",
        "inav",
        "--loop",
    ]
    assert default.stderr == ""


def test_evening_plist_prints_the_muhurat_note_on_stderr_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cli.ops, "find_uv", lambda: Path("/opt/homebrew/bin/uv"))
    result = runner.invoke(cli.app, ["ops", "launchd-plist", "--job", "daily-evening"])
    assert "Muhurat" in result.stderr
    assert "Muhurat" not in result.stdout  # the plist can be redirected into a file as is
    plistlib.loads(result.stdout.encode())


def test_launchd_plist_unknown_job_is_a_usage_error() -> None:
    result = runner.invoke(cli.app, ["ops", "launchd-plist", "--job", "nightly"])
    assert result.exit_code == 2
    assert "daily-evening" in result.stderr
