"""events: data/manual/events.csv -> clean table (requests R5, R16)."""

from __future__ import annotations

import logging
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import pytest

from etfp.clean import CLEANERS, CleanContext, CleanError, make_context
from etfp.clean.manual_events import (
    COLUMNS,
    build,
    compute_first_tradable_session,
    make_event_id,
)
from etfp.config import load_etfs
from etfp.store import Store
from etfp.timeutil import IST

HEADER = (
    "event_id,date,ts_ist,type,category,direction,symbols,first_tradable_session,"
    "description,url,source_primary,verified"
)


def make_ctx(tmp_path: Path, csv_text: str | None) -> CleanContext:
    """A throwaway project root; the events file is written only if csv_text is given."""
    (tmp_path / "data" / "raw").mkdir(parents=True, exist_ok=True)
    if csv_text is not None:
        (tmp_path / "data" / "manual").mkdir(exist_ok=True)
        (tmp_path / "data" / "manual" / "events.csv").write_text(csv_text, encoding="utf-8")
    return CleanContext(raw_root=tmp_path / "data" / "raw", universe=load_etfs())


def one(tmp_path: Path, **fields: str) -> pd.DataFrame:
    base = {
        "event_id": "e1",
        "date": "2026-09-25",
        "ts_ist": "",
        "type": "warning",
        "category": "caution",
        "direction": "",
        "symbols": "",
        "first_tradable_session": "",
        "description": "something happened",
        "url": "",
        "source_primary": "",
        "verified": "",
    } | fields
    line = ",".join(f'"{base[c]}"' for c in HEADER.split(","))
    return build(make_ctx(tmp_path, f"{HEADER}\n{line}\n"))


# -- the first tradable session ---------------------------------------------------------


@pytest.mark.parametrize(
    ("day", "stamp", "expected"),
    [
        # 2026-09-25 is a Friday and an NSE session.
        (date(2026, 9, 25), "2026-09-25T16:00", date(2026, 9, 28)),  # after the close -> Monday
        (date(2026, 9, 25), "2026-09-25T15:30", date(2026, 9, 28)),  # at the close: too late
        (date(2026, 9, 25), "2026-09-25T15:29", date(2026, 9, 25)),  # one minute before
        (date(2026, 9, 25), "2026-09-25T08:00", date(2026, 9, 25)),  # before the open: same day
        (date(2026, 9, 26), "2026-09-26T11:00", date(2026, 9, 28)),  # Saturday -> Monday
        (date(2026, 1, 26), "2026-01-26T10:00", date(2026, 1, 27)),  # Republic Day holiday
        (
            date(2026, 9, 25),
            "2026-09-25T10:40+00:00",
            date(2026, 9, 28),
        ),  # 16:10 IST written in UTC
    ],
)
def test_first_tradable_session_with_a_time(day: date, stamp: str, expected: date) -> None:
    ts = datetime.fromisoformat(stamp)
    ts = ts.replace(tzinfo=IST) if ts.tzinfo is None else ts
    assert compute_first_tradable_session(day, ts) == expected


@pytest.mark.parametrize(
    ("day", "expected"),
    [
        (date(2026, 9, 25), date(2026, 9, 25)),  # unknown time on a session: same day
        (date(2026, 9, 26), date(2026, 9, 28)),  # Saturday
        (date(2026, 1, 26), date(2026, 1, 27)),  # holiday
    ],
)
def test_first_tradable_session_without_a_time(day: date, expected: date) -> None:
    assert compute_first_tradable_session(day, None) == expected


def test_a_blank_first_session_is_computed_from_the_time(tmp_path: Path) -> None:
    out = one(tmp_path, ts_ist="2026-09-25 16:10")
    assert out.loc[0, "first_tradable_session"] == date(2026, 9, 28)
    assert out.loc[0, "ts_ist"] == pd.Timestamp("2026-09-25 16:10", tz="Asia/Kolkata")


def test_a_time_written_with_an_offset_is_stored_in_ist(tmp_path: Path) -> None:
    out = one(tmp_path, ts_ist="2026-09-25T10:40:00+00:00")
    assert str(out.loc[0, "ts_ist"].tz) == "Asia/Kolkata"
    assert out.loc[0, "ts_ist"].hour == 16 and out.loc[0, "ts_ist"].minute == 10


def test_an_explicit_first_session_is_kept(tmp_path: Path) -> None:
    out = one(tmp_path, first_tradable_session="2026-09-28")
    assert out.loc[0, "first_tradable_session"] == date(2026, 9, 28)


def test_events_before_the_calendar_starts_get_no_computed_session(tmp_path: Path) -> None:
    out = one(tmp_path, date="2020-06-01")
    assert pd.isna(out.loc[0, "first_tradable_session"])


# -- validation errors name the file and the line ---------------------------------------


@pytest.mark.parametrize(
    ("fields", "needle"),
    [
        ({"category": "gossip"}, "'category': 'gossip' must be one of"),
        ({"direction": "up"}, "'direction': 'up' must be one of"),
        ({"type": "boom"}, "'type': 'boom' must be one of"),
        ({"date": "25/09/2026"}, "not a date YYYY-MM-DD"),
        ({"description": ""}, "'description' is required"),
        ({"symbols": "MONQ50;NOPE"}, "unknown ETF symbol 'NOPE'"),
        ({"verified": "maybe"}, "must be true or false"),
        ({"ts_ist": "yesterday"}, "not a time like"),
        ({"ts_ist": "2026-09-26T09:00"}, "falls on 2026-09-26"),
        ({"first_tradable_session": "2026-09-26"}, "not an NSE trading day"),
        ({"first_tradable_session": "2026-09-24"}, "before the event date"),
        ({"verified": "true"}, "source_primary is empty"),
        ({"verified": "true", "source_primary": "data/raw/rules/nope.pdf"}, "not an existing file"),
        ({"url": "http://a b"}, "contains a space"),
        ({"date": "2031-01-02"}, "cannot work out the trading session"),
    ],
)
def test_bad_values_fail_loudly(tmp_path: Path, fields: dict[str, str], needle: str) -> None:
    with pytest.raises(CleanError) as err:
        one(tmp_path, **fields)
    message = str(err.value)
    assert needle in message
    assert "events: data/manual/events.csv line 2:" in message


def test_the_line_number_counts_from_the_header(tmp_path: Path) -> None:
    good = '"a","2026-09-25","","warning","caution","","","","fine","","",""'
    bad = '"b","2026-09-25","","warning","caution","","","","fine","","","perhaps"'
    ctx = make_ctx(tmp_path, f"{HEADER}\n{good}\n\n{bad}\n")  # a blank line in between
    with pytest.raises(CleanError, match=r"events\.csv line 4: .*'verified'"):
        build(ctx)


def test_a_multiline_quoted_description_is_one_row(tmp_path: Path) -> None:
    first = 'e1,2026-09-25,,warning,caution,,,,"two\nlines",,,'  # spans file lines 2-3
    second = "e2,2026-09-25,,warning,caution,,,,ok,,,nope"  # bad 'verified' on line 4
    text = f"{HEADER}\n{first}\n{second}\n"
    with pytest.raises(CleanError, match=r"line 4"):
        build(make_ctx(tmp_path, text))


def test_verified_true_with_an_existing_file_passes(tmp_path: Path) -> None:
    doc = tmp_path / "data" / "raw" / "rules"
    doc.mkdir(parents=True)
    (doc / "circular.pdf").write_bytes(b"%PDF")
    out = one(tmp_path, verified="TRUE", source_primary="data/raw/rules/circular.pdf")
    assert bool(out.loc[0, "verified"]) is True


def test_verified_true_with_a_link_passes(tmp_path: Path) -> None:
    out = one(tmp_path, verified="yes", source_primary="https://www.sebi.gov.in/x")
    assert bool(out.loc[0, "verified"]) is True


def test_unknown_and_missing_header_columns_are_errors(tmp_path: Path) -> None:
    with pytest.raises(CleanError, match=r"unknown column.*colour"):
        build(make_ctx(tmp_path, "date,type,category,description,colour\n"))
    other = tmp_path / "b"
    other.mkdir()
    with pytest.raises(CleanError, match=r"missing required column.*description"):
        build(make_ctx(other, "date,type,category\n"))


def test_a_row_with_too_many_fields_names_the_line(tmp_path: Path) -> None:
    text = f"{HEADER}\ne1,2026-09-25,,warning,caution,,,,hello, world,,,,\n"
    with pytest.raises(CleanError, match=r"line 2: 14 fields but the header has 12"):
        build(make_ctx(tmp_path, text))


def test_duplicate_event_ids_are_errors(tmp_path: Path) -> None:
    row = "e1,2026-09-25,,warning,caution,,,,x,,,"
    with pytest.raises(
        CleanError, match=r"'e1' appears twice: .*events\.csv:2 and .*events\.csv:3"
    ):
        build(make_ctx(tmp_path, f"{HEADER}\n{row}\n{row}\n"))


# -- ids, symbols, direction ------------------------------------------------------------


def test_generated_ids_are_stable_and_follow_the_content(tmp_path: Path) -> None:
    a = one(tmp_path / "a", event_id="")
    again = one(tmp_path / "b", event_id="")
    assert a.loc[0, "event_id"] == again.loc[0, "event_id"]
    assert a.loc[0, "event_id"] == make_event_id(
        date(2026, 9, 25), "caution", [], "something happened"
    )
    assert a.loc[0, "event_id"].startswith("2026-09-25_caution_")
    assert make_event_id(date(2026, 9, 25), "caution", [], "other text") != a.loc[0, "event_id"]


def test_symbols_are_normalised_to_a_semicolon_list(tmp_path: Path) -> None:
    out = one(tmp_path, symbols=" monq50 , MON100;MONQ50")
    assert out.loc[0, "symbols"] == "MONQ50;MON100"


def test_a_blank_direction_is_kept_as_not_classified(tmp_path: Path) -> None:
    assert pd.isna(one(tmp_path / "a").loc[0, "direction"])
    assert one(tmp_path / "b", direction="neutral").loc[0, "direction"] == "neutral"


# -- missing file, store, seed file -------------------------------------------------------


def test_a_missing_file_gives_an_empty_table(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger="etfp.clean"):
        out = build(make_ctx(tmp_path, None))
    assert out.empty and list(out.columns) == COLUMNS
    assert "events.csv not found" in caplog.text


def test_a_header_only_file_gives_an_empty_table(tmp_path: Path) -> None:
    assert build(make_ctx(tmp_path, HEADER + "\n")).empty


def test_the_table_round_trips_through_the_store(tmp_path: Path) -> None:
    out = one(tmp_path, ts_ist="2026-09-25T16:10", symbols="MONQ50", direction="neutral")
    store = Store(tmp_path / "clean")
    store.write_table("events", out)
    back = store.read_table("events").iloc[0]
    assert back["ts_ist"] == pd.Timestamp("2026-09-25 16:10", tz="Asia/Kolkata")
    assert back["first_tradable_session"] == date(2026, 9, 28)
    assert not back["verified"]
    assert back["source_file"] == "data/manual/events.csv:2"
    store.write_table("events", build(make_ctx(tmp_path / "empty", None)))  # empty is accepted


def test_the_events_cleaner_is_registered_after_etf_master() -> None:
    order = [t for t, _ in CLEANERS]
    assert order.index("events") > order.index("etf_master")


def test_the_shipped_seed_file_loads() -> None:
    """The repo's own data/manual/events.csv must always parse and be self-consistent."""
    out = build(make_context())
    assert len(out) >= 6 and out["event_id"].is_unique
    rows = out.set_index("event_id")
    bse = rows.loc["bse_2026-09-25_caution"]
    assert bse["category"] == "caution" and bse["first_tradable_session"] == date(2026, 9, 28)
    assert rows.loc["rule_2026-09-07_prev_close_regime", "first_tradable_session"] == date(
        2026, 9, 7
    )
    sip = [i for i in rows.index if i.startswith("motilal_fof_")]
    assert len(sip) == 2 and set(rows.loc[sip, "symbols"]) == {"MON100"}
    # Nothing in the seed is claimed as checked against a primary document yet.
    assert not rows["verified"].any()
    assert (out["first_tradable_session"] >= out["date"]).all()
