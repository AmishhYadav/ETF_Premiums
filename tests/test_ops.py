"""Tests for the launchd plist and the status report. Nothing here touches the machine."""

from __future__ import annotations

import plistlib
from datetime import date, datetime
from pathlib import Path

import pytest

from etfp import ops, recorder
from etfp.rawstore import RawStore
from etfp.timeutil import IST

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
