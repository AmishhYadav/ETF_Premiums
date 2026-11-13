"""CLI tests with typer's CliRunner.

Internals are replaced by fakes: no network, no real sleeping.
"""

from __future__ import annotations

import logging
import plistlib
import re
from datetime import date, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from etfp import __version__, cli, recorder
from etfp.config import load_collect_settings
from etfp.recorder import SourceOutcome, TickResult
from etfp.timeutil import IST

runner = CliRunner()
REAL_SETTINGS = load_collect_settings()  # read once, before tests redirect the project root
HOLIDAY = date(2026, 11, 10)
SESSION_DAY = date(2026, 11, 11)


@pytest.fixture(autouse=True)
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Point every project path at tmp_path and keep real logging handlers out of the way."""
    monkeypatch.setenv("ETFP_ROOT", str(tmp_path))
    monkeypatch.setattr(cli, "load_collect_settings", lambda: REAL_SETTINGS)
    monkeypatch.setattr(cli, "_configure_logging", lambda: None)
    return tmp_path


def outcome(source: str, ok: bool) -> SourceOutcome:
    if ok:
        return SourceOutcome(
            source, True, f"{source}/2026-11-11/inav_091500.json", 1234, None, None, False
        )
    return SourceOutcome(
        source, False, None, None, "SourceBlockedError", "blocked (HTTP 403)", False
    )


def tick(*oks: bool) -> TickResult:
    stamp = datetime(2026, 11, 11, 9, 15, tzinfo=IST)
    outs = tuple(outcome(f"s{i}", ok) for i, ok in enumerate(oks))
    return TickResult(stamp, stamp, None, outs)


def test_version_still_works() -> None:
    result = runner.invoke(cli.app, ["version"])
    assert result.exit_code == 0
    assert result.output.strip() == __version__


@pytest.mark.parametrize("args", [["collect", "inav"], ["collect", "inav", "--once", "--loop"]])
def test_exactly_one_of_once_or_loop(args: list[str], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(recorder, "run_tick", lambda *a, **k: pytest.fail("must not run"))
    result = runner.invoke(cli.app, args)
    assert result.exit_code == 2
    assert "exactly one of --once or --loop" in result.output


def test_once_refuses_on_a_closed_day_without_force(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "today_ist", lambda: HOLIDAY)
    monkeypatch.setattr(recorder, "run_tick", lambda *a, **k: pytest.fail("must not run"))
    result = runner.invoke(cli.app, ["collect", "inav", "--once"])
    assert result.exit_code == 2
    assert "closed" in result.output
    assert "--force" in result.output


def test_once_prints_one_line_per_source_and_exit_0_if_any_ok(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict] = []

    def fake_tick(client, store, **kwargs):
        calls.append(kwargs)
        return tick(True, False)

    monkeypatch.setattr(cli, "today_ist", lambda: HOLIDAY)
    monkeypatch.setattr(recorder, "run_tick", fake_tick)
    result = runner.invoke(cli.app, ["collect", "inav", "--once", "--force"])
    assert result.exit_code == 0
    lines = result.output.strip().splitlines()
    assert lines == [
        "OK s0/2026-11-11/inav_091500.json 1234",
        "FAILED SourceBlockedError: blocked (HTTP 403)",
    ]
    assert calls == [{}]  # scheduled_for stays None


def test_once_exit_1_when_every_source_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "today_ist", lambda: SESSION_DAY)
    monkeypatch.setattr(recorder, "run_tick", lambda *a, **k: tick(False, False))
    result = runner.invoke(cli.app, ["collect", "inav", "--once"])
    assert result.exit_code == 1
    assert result.output.count("FAILED") == 2


def test_loop_passes_force_and_exits_0(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict = {}

    def fake_loop(**kwargs):
        seen.update(kwargs)
        return []

    monkeypatch.setattr(recorder, "run_loop", fake_loop)
    result = runner.invoke(cli.app, ["collect", "inav", "--loop", "--force"])
    assert result.exit_code == 0
    assert seen["force"] is True
    assert seen["settings"] is REAL_SETTINGS
    assert seen["store"].root.name == "raw"
    assert "0 ticks" in result.output


def test_loop_exit_3_when_another_recorder_holds_the_lock(monkeypatch: pytest.MonkeyPatch) -> None:
    def locked(**kwargs):
        raise recorder.RecorderAlreadyRunningError("another iNAV recorder is already running")

    monkeypatch.setattr(recorder, "run_loop", locked)
    result = runner.invoke(cli.app, ["collect", "inav", "--loop"])
    assert result.exit_code == 3
    assert "already running" in result.output


def test_client_is_built_from_settings() -> None:
    with cli._build_client(REAL_SETTINGS) as client:
        assert client._max_retries == REAL_SETTINGS.http.max_retries
        assert client._intervals["nseindia.com"] == REAL_SETTINGS.http.nse_min_interval_s
        assert client._default_interval_s == REAL_SETTINGS.http.default_interval_s


def test_status_with_nothing_recorded() -> None:
    result = runner.invoke(cli.app, ["status"])
    assert result.exit_code == 0
    assert "no ticks recorded yet" in result.output
    assert "failures logged today" in result.output


def test_status_shows_a_heartbeat(isolated: Path) -> None:
    state = isolated / "data" / "state"
    recorder.write_atomic(
        state / "inav_heartbeat.json",
        {
            "last_tick_started": datetime.now(tz=IST).isoformat(),
            "last_tick_finished": datetime.now(tz=IST).isoformat(),
            "scheduled_for": "09:15",
            "ok": 1,
            "failed": 0,
            "sources": {"amc_inav_mirae": {"ok": True, "last_ok_at": "2026-11-11T09:15:00+05:30"}},
        },
    )
    result = runner.invoke(cli.app, ["status"])
    assert result.exit_code == 0
    assert "no ticks recorded yet" not in result.output
    assert "OK     amc_inav_mirae" in result.output


def test_launchd_plist_prints_valid_plist(monkeypatch: pytest.MonkeyPatch, isolated: Path) -> None:
    monkeypatch.setattr(cli.ops, "find_uv", lambda: Path("/opt/homebrew/bin/uv"))
    result = runner.invoke(cli.app, ["ops", "launchd-plist"])
    assert result.exit_code == 0
    plist = plistlib.loads(result.output.encode())
    assert plist["ProgramArguments"][:3] == ["/usr/bin/caffeinate", "-i", "/opt/homebrew/bin/uv"]
    assert plist["EnvironmentVariables"]["ETFP_ROOT"] == str(isolated.resolve())


def test_launchd_plist_without_uv_is_a_clear_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing():
        raise FileNotFoundError("could not find 'uv' on PATH")

    monkeypatch.setattr(cli.ops, "find_uv", missing)
    result = runner.invoke(cli.app, ["ops", "launchd-plist"])
    assert result.exit_code == 2
    assert "uv" in result.output


def test_logging_goes_to_stderr_and_rotating_file_with_ist_offset(
    isolated: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.undo()  # drop the stub from the autouse fixture
    monkeypatch.setenv("ETFP_ROOT", str(isolated))
    logger = logging.getLogger("etfp")
    before = list(logger.handlers)
    try:
        cli._configure_logging()
        cli._configure_logging()  # twice: must not duplicate handlers
        ours = [h for h in logger.handlers if h not in before]
        assert len(ours) == 2
        assert any(isinstance(h, logging.handlers.RotatingFileHandler) for h in ours)
        file_handler = next(h for h in ours if isinstance(h, logging.handlers.RotatingFileHandler))
        assert file_handler.maxBytes == 5 * 1024 * 1024
        assert file_handler.backupCount == 5
        logging.getLogger("etfp.recorder").info("hello from the test")
        for h in ours:
            h.flush()
        text = (isolated / "logs" / "collect.log").read_text()
        assert "hello from the test" in text
        assert re.search(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\+05:30 INFO etfp\.recorder:", text, re.M)
    finally:
        for h in list(logger.handlers):
            if h not in before:
                logger.removeHandler(h)
                h.close()
