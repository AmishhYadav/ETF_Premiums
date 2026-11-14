"""CLI tests for `etfp collect daily` and `etfp backfill` (typer CliRunner, no network).

A fake web server (httpx.MockTransport) answers every request; the project root is a temp
folder, and "now" is pinned to Monday 2026-11-09 20:00 IST. NSE was closed on
Fri 2026-10-02 (Gandhi Jayanti).
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

from etfp import cli
from etfp.config import load_collect_settings
from etfp.http import PoliteClient
from etfp.timeutil import IST

runner = CliRunner()
SETTINGS = load_collect_settings()
NOW = datetime(2026, 11, 9, 20, 0, tzinfo=IST)


class Web:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.status: dict[str, int] = {}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        url = str(request.url)
        for needle, code in self.status.items():
            if needle in url:
                return httpx.Response(code, content=b"nope")
        if "finance.yahoo.com" in url:
            return httpx.Response(200, content=b'{"chart":{"result":[{"meta":{}}],"error":null}}')
        return httpx.Response(200, content=b"body")

    def count(self, needle: str) -> int:
        return sum(needle in str(r.url) for r in self.requests)


@pytest.fixture
def web(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Web:
    fake = Web()
    monkeypatch.setenv("ETFP_ROOT", str(tmp_path))
    monkeypatch.setattr(cli, "load_collect_settings", lambda: SETTINGS)
    monkeypatch.setattr(cli, "_configure_logging", lambda: None)
    monkeypatch.setattr(cli, "now_ist", lambda: NOW)
    monkeypatch.setattr(
        cli,
        "_build_bulk_client",
        lambda settings: PoliteClient(
            min_interval_s={"nseindia.com": 1.5},
            transport=httpx.MockTransport(fake),
            sleep=lambda s: None,
            max_retries=0,
        ),
    )
    return fake


def amfi_day(text: str) -> date:
    """01-Oct-2026 -> date(2026, 10, 1)."""
    return datetime.strptime(text, "%d-%b-%Y").replace(tzinfo=IST).date()


def raw_files(root: Path) -> list[str]:
    base = root / "data" / "raw"
    return sorted(
        str(p.relative_to(base)) for p in base.rglob("*") if p.is_file() and p.suffix != ".jsonl"
    )


# -- collect daily ----------------------------------------------------------------------


def test_daily_downloads_all_thirteen_files_then_is_idempotent(web: Web, tmp_path: Path):
    result = runner.invoke(cli.app, ["collect", "daily", "--date", "2026-11-09"])
    assert result.exit_code == 0, result.output
    assert "summary: 13 fetched, 0 already stored, 0 not available, 0 FAILED" in result.output
    files = raw_files(tmp_path)
    assert "nse_bhavcopy_full/2026-11-09/sec_bhavdata_full_09112026.csv" in files
    assert "amfi_nav_history/2026-11-09/navhistory_20261106_20261109.txt" in files
    assert "yahoo_intraday/2026-11-09/HKDINR_X_5m.json" in files
    assert "bse_bhavcopy/2026-11-09/BhavCopy_BSE_CM_0_0_0_20261109_F_0000.CSV" in files
    assert "nse_slb_bhavcopy/2026-11-09/SLBM_BC_09112026.DAT" in files
    assert len(files) == 13 and len(web.requests) == 13

    again = runner.invoke(cli.app, ["collect", "daily", "--date", "2026-11-09"])
    assert again.exit_code == 0
    assert "summary: 0 fetched, 13 already stored" in again.output
    assert len(web.requests) == 13  # the second run asked nobody


def test_daily_defaults_to_today_in_india(web: Web, tmp_path: Path):
    result = runner.invoke(cli.app, ["collect", "daily", "--sources", "bhavcopy"])
    assert result.exit_code == 0, result.output
    assert raw_files(tmp_path) == ["nse_bhavcopy_full/2026-11-09/sec_bhavdata_full_09112026.csv"]


@pytest.mark.parametrize(("day", "weekday"), [("2026-11-08", "Sunday"), ("2026-10-02", "Friday")])
def test_daily_skips_days_nse_was_closed_with_a_clear_message(web: Web, day: str, weekday: str):
    result = runner.invoke(cli.app, ["collect", "daily", "--date", day])
    assert result.exit_code == 0
    assert f"NSE was closed on {day} ({weekday})" in result.output
    assert web.requests == []


def test_daily_one_failure_is_loud_and_nonzero_but_others_still_run(web: Web, tmp_path: Path):
    web.status["BhavCopy_NSE_CM"] = 500
    result = runner.invoke(cli.app, ["collect", "daily", "--date", "2026-11-09"])
    assert result.exit_code == 1
    assert "FAILED      nse_udiff_bhavcopy 2026-11-09" in result.stderr
    assert "HTTP 500" in result.stderr
    assert "12 fetched" in result.stdout and "1 FAILED" in result.stdout
    assert len(raw_files(tmp_path)) == 12


def test_daily_404_is_a_failure_not_silently_skipped(web: Web):
    web.status["sec_bhavdata_full"] = 404
    result = runner.invoke(
        cli.app, ["collect", "daily", "--date", "2026-11-09", "--sources", "bhavcopy"]
    )
    assert result.exit_code == 1
    assert "FAILED      nse_bhavcopy_full 2026-11-09" in result.stderr


def test_daily_past_date_notes_what_cannot_be_fetched_for_a_past_day(web: Web):
    result = runner.invoke(cli.app, ["collect", "daily", "--date", "2026-11-06"])
    assert result.exit_code == 0, result.output
    assert "NOTE        nse_etf_seclist 2026-11-06: skipped" in result.output
    assert "NOTE        amfi_navall 2026-11-06: skipped" in result.output
    assert "11 fetched" in result.output  # 4 NSE/AMFI + 4 Yahoo + 1 BSE + 2 SLB


def test_daily_force_downloads_again(web: Web):
    runner.invoke(cli.app, ["collect", "daily", "--date", "2026-11-09", "--sources", "bhavcopy"])
    result = runner.invoke(
        cli.app, ["collect", "daily", "--date", "2026-11-09", "--sources", "bhavcopy", "--force"]
    )
    assert result.exit_code == 0 and "identical to stored copy" in result.output
    assert web.count("sec_bhavdata_full") == 2


def test_daily_sources_filter_accepts_commas_and_repeats(web: Web):
    result = runner.invoke(
        cli.app,
        [
            "collect",
            "daily",
            "--date",
            "2026-11-09",
            "--sources",
            "udiff,secmaster",
            "--sources",
            "yahoo",
        ],
    )
    assert result.exit_code == 0 and "6 fetched" in result.output  # 1 + 1 + 4


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["--date", "2026-11-12"], "in the future"),
        (["--date", "09-11-2026"], "--date must be a date like"),
        (["--date", "2020-06-01"], "before the project sample start"),
        (["--sources", "nse"], "unknown source 'nse'"),
    ],
)
def test_daily_usage_errors_exit_2_and_request_nothing(web: Web, args: list[str], message: str):
    result = runner.invoke(cli.app, ["collect", "daily", *args])
    assert result.exit_code == 2
    assert message in result.output
    assert web.requests == []


# -- backfill ---------------------------------------------------------------------------


def test_backfill_fetches_trading_days_only_and_resumes(web: Web, tmp_path: Path):
    args = ["backfill", "--from", "2026-11-02", "--to", "2026-11-09", "--sources", "bhavcopy"]
    result = runner.invoke(cli.app, args)
    assert result.exit_code == 0, result.output
    assert "summary: 6 fetched" in result.output  # Mon 2..Fri 6, Mon 9
    assert len(raw_files(tmp_path)) == 6
    again = runner.invoke(cli.app, args)
    assert "summary: 0 fetched, 6 already stored" in again.output
    assert web.count("sec_bhavdata_full") == 6


def test_backfill_records_404_as_not_available_and_does_not_ask_again(web: Web, tmp_path: Path):
    web.status["BhavCopy_NSE_CM"] = 404
    args = ["backfill", "--from", "2026-11-06", "--to", "2026-11-09", "--sources", "udiff"]
    first = runner.invoke(cli.app, args)
    assert first.exit_code == 0, first.output
    assert "UNAVAILABLE nse_udiff_bhavcopy 2026-11-06" in first.output
    assert "2 not available" in first.output
    state = tmp_path / "data" / "state" / "backfill_state.json"
    assert state.exists()
    n = len(web.requests)

    second = runner.invoke(cli.app, args)
    assert second.exit_code == 0 and len(web.requests) == n
    assert "UNAVAILABLE" not in second.output  # known ones are counted, not repeated
    assert "2 not available" in second.output

    web.status.clear()
    retry = runner.invoke(cli.app, [*args, "--retry-unavailable"])
    assert retry.exit_code == 0 and "2 fetched" in retry.output
    assert len(web.requests) == n + 2


def test_backfill_amfi_uses_small_windows(web: Web):
    result = runner.invoke(
        cli.app,
        ["backfill", "--from", "2026-10-01", "--to", "2026-10-31", "--sources", "amfi_history"],
    )
    assert result.exit_code == 0, result.output
    assert len(web.requests) >= 5
    for request in web.requests:
        start = amfi_day(request.url.params["frmdt"])
        end = amfi_day(request.url.params["todt"])
        assert 0 <= (end - start).days <= 6
    assert amfi_day(web.requests[0].url.params["frmdt"]) == date(2026, 10, 1)
    assert amfi_day(web.requests[-1].url.params["todt"]) == date(2026, 10, 31)


def test_backfill_failure_gives_nonzero_exit_and_other_sources_continue(web: Web, tmp_path: Path):
    web.status["sec_bhavdata_full"] = 500
    result = runner.invoke(
        cli.app,
        [
            "backfill",
            "--from",
            "2026-11-09",
            "--to",
            "2026-11-09",
            "--sources",
            "bhavcopy,secmaster",
        ],
    )
    assert result.exit_code == 1
    assert "FAILED      nse_bhavcopy_full 2026-11-09" in result.stderr
    assert "nse_security_master/2026-11-09/NSE_CM_security_09112026.csv.gz" in raw_files(tmp_path)


def test_backfill_block_stops_nse_requests(web: Web):
    web.status["nsearchives"] = 429
    result = runner.invoke(
        cli.app, ["backfill", "--from", "2026-11-02", "--to", "2026-11-09", "--sources", "bhavcopy"]
    )
    assert result.exit_code == 1
    assert web.count("nsearchives") == 1
    assert "blocked" in result.stderr


def test_backfill_dry_run_counts_without_requesting(web: Web, tmp_path: Path):
    runner.invoke(
        cli.app, ["backfill", "--from", "2026-11-02", "--to", "2026-11-04", "--sources", "bhavcopy"]
    )
    n = len(web.requests)
    result = runner.invoke(
        cli.app,
        [
            "backfill",
            "--from",
            "2026-11-02",
            "--to",
            "2026-11-09",
            "--sources",
            "bhavcopy",
            "--dry-run",
        ],
    )
    assert result.exit_code == 0
    assert "dry run: 3 to download, 3 already stored, 0 known not available" in result.output
    assert len(web.requests) == n


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["--from", "2026-11-09", "--to", "2026-11-02"], "after --to"),
        (["--from", "nov 2"], "--from must be a date like"),
        (["--from", "2020-01-01"], "before the project sample start"),
        (["--from", "2026-11-02", "--sources", "navall"], "unknown source 'navall'"),
    ],
)
def test_backfill_usage_errors_exit_2(web: Web, args: list[str], message: str):
    result = runner.invoke(cli.app, ["backfill", *args])
    assert result.exit_code == 2
    assert message in result.output
    assert web.requests == []


def test_backfill_requires_from():
    result = runner.invoke(cli.app, ["backfill"])
    assert result.exit_code == 2
