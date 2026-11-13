"""Tests for the immutable raw store. No network."""

from __future__ import annotations

import hashlib
import os
import stat
from datetime import date, datetime
from pathlib import Path

import pytest

from etfp.http import FetchResult
from etfp.rawstore import RawStore
from etfp.timeutil import IST

DAY = date(2026, 11, 9)
FETCHED = datetime(2026, 11, 10, 18, 30, tzinfo=IST)
MANIFEST_KEYS = {
    "source",
    "day",
    "name",
    "relpath",
    "sha256",
    "size_bytes",
    "url",
    "status_code",
    "fetched_at",
    "version",
    "duplicate",
}


def _save(store: RawStore, name: str, content: bytes, source: str = "nse_test"):
    return store.save(
        source=source,
        day=DAY,
        name=name,
        content=content,
        url="https://example.test/file",
        status_code=200,
        fetched_at=FETCHED,
    )


def _leftovers(root: Path) -> list[Path]:
    return [p for p in root.rglob("*") if p.name.startswith(".tmp-")]


def test_save_writes_exact_bytes_readonly_and_manifest(tmp_path: Path) -> None:
    store = RawStore(tmp_path / "raw")
    content = b"SYMBOL,PRICE\r\nABC,1.50\r\n\xff\xfe"  # CRLF and non-UTF8 must survive
    rec = _save(store, "a.csv", content)

    expected = tmp_path / "raw" / "nse_test" / "2026-11-09" / "a.csv"
    assert rec.path == expected
    assert expected.read_bytes() == content
    assert not os.access(expected, os.W_OK) or os.geteuid() == 0
    assert stat.S_IMODE(expected.stat().st_mode) == 0o444

    assert rec.source == "nse_test"
    assert rec.day == DAY
    assert rec.name == "a.csv"
    assert rec.relpath == "nse_test/2026-11-09/a.csv"
    assert rec.sha256 == hashlib.sha256(content).hexdigest()
    assert rec.size_bytes == len(content)
    assert rec.url == "https://example.test/file"
    assert rec.status_code == 200
    assert rec.fetched_at == FETCHED
    assert rec.version == 1
    assert rec.duplicate is False

    entries = list(store.iter_manifest())
    assert len(entries) == 1
    assert set(entries[0]) == MANIFEST_KEYS
    assert entries[0]["day"] == "2026-11-09"
    assert entries[0]["fetched_at"] == "2026-11-10T18:30:00+05:30"
    assert entries[0]["sha256"] == rec.sha256
    assert entries[0]["duplicate"] is False
    lines = store.manifest_path().read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1


def test_identical_content_is_duplicate_and_writes_nothing(tmp_path: Path) -> None:
    store = RawStore(tmp_path)
    first = _save(store, "a.csv", b"same")
    mtime = first.path.stat().st_mtime_ns
    second = _save(store, "a.csv", b"same")

    assert second.duplicate is True
    assert second.path == first.path
    assert second.version == 1
    assert first.path.read_bytes() == b"same"
    assert first.path.stat().st_mtime_ns == mtime
    assert sorted(p.name for p in first.path.parent.iterdir()) == ["a.csv"]

    entries = list(store.iter_manifest())
    assert [e["duplicate"] for e in entries] == [False, True]
    assert store.manifest_path().read_text(encoding="utf-8").count("\n") == 2


@pytest.mark.parametrize(
    ("name", "v2", "v3"),
    [
        ("a.csv", "a.v2.csv", "a.v3.csv"),
        ("b.csv.gz", "b.v2.csv.gz", "b.v3.csv.gz"),
        ("plain", "plain.v2", "plain.v3"),
    ],
)
def test_changed_content_creates_new_versions(tmp_path: Path, name: str, v2: str, v3: str) -> None:
    store = RawStore(tmp_path)
    r1 = _save(store, name, b"one")
    r2 = _save(store, name, b"two")

    assert r2.name == v2
    assert r2.version == 2
    assert r2.duplicate is False
    assert r1.path.read_bytes() == b"one"  # version 1 untouched
    assert r2.path.read_bytes() == b"two"
    assert store.latest("nse_test", DAY, name) == r2.path
    assert store.exists("nse_test", DAY, name)

    # Re-downloading the newest content is a duplicate of v2, not a new v3.
    again = _save(store, name, b"two")
    assert again.duplicate is True
    assert again.path == r2.path

    r3 = _save(store, name, b"three")
    assert r3.name == v3
    assert r3.version == 3
    assert store.latest("nse_test", DAY, name) == r3.path
    assert r1.path.read_bytes() == b"one"
    assert r2.path.read_bytes() == b"two"


def test_older_content_after_change_is_stored_as_new_version(tmp_path: Path) -> None:
    """A revert to v1's bytes is still a change from the latest, so it is kept as v3."""
    store = RawStore(tmp_path)
    _save(store, "a.csv", b"one")
    _save(store, "a.csv", b"two")
    r3 = _save(store, "a.csv", b"one")
    assert r3.version == 3
    assert not r3.duplicate


@pytest.mark.parametrize("bad", ["../x", "a/b", "", ".hidden", "a\\b", "x..y"])
def test_invalid_source_and_name_rejected(tmp_path: Path, bad: str) -> None:
    store = RawStore(tmp_path)
    with pytest.raises(ValueError):
        _save(store, "ok.csv", b"x", source=bad)
    with pytest.raises(ValueError):
        _save(store, bad, b"x")
    assert not list(tmp_path.rglob("*"))  # nothing was created


def test_naive_fetched_at_rejected(tmp_path: Path) -> None:
    store = RawStore(tmp_path)
    with pytest.raises(ValueError, match="timezone-aware"):
        store.save(
            source="s",
            day=DAY,
            name="a.csv",
            content=b"x",
            url="u",
            status_code=200,
            fetched_at=datetime(2026, 11, 10, 18, 30),  # noqa: DTZ001 - the point of the test
        )
    assert not list(tmp_path.rglob("*"))


def test_no_temp_files_after_success(tmp_path: Path) -> None:
    store = RawStore(tmp_path)
    _save(store, "a.csv", b"1")
    _save(store, "a.csv", b"2")
    assert _leftovers(tmp_path) == []


def test_no_temp_files_or_manifest_after_write_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = RawStore(tmp_path)

    def boom(fd: int) -> None:
        raise OSError("disk on fire")

    monkeypatch.setattr(os, "fsync", boom)
    with pytest.raises(OSError, match="disk on fire"):
        _save(store, "a.csv", b"1")
    monkeypatch.undo()

    assert _leftovers(tmp_path) == []
    assert not store.exists("nse_test", DAY, "a.csv")
    assert list(store.iter_manifest()) == []


def test_race_never_overwrites_existing_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the target appears between the check and the link, we must not clobber it."""
    store = RawStore(tmp_path)
    target = store.path_for("nse_test", DAY, "a.csv")
    real_link = os.link
    calls = {"n": 0}

    def racing_link(src, dst, *args, **kwargs):
        if calls["n"] == 0:
            calls["n"] += 1
            Path(dst).write_bytes(b"winner")  # another process got there first
        return real_link(src, dst, *args, **kwargs)

    monkeypatch.setattr(os, "link", racing_link)
    rec = _save(store, "a.csv", b"loser")
    monkeypatch.undo()

    assert target.read_bytes() == b"winner"
    assert rec.version == 2
    assert rec.path.read_bytes() == b"loser"
    assert _leftovers(tmp_path) == []


def test_exists_latest_path_for(tmp_path: Path) -> None:
    store = RawStore(tmp_path / "raw")
    assert not store.exists("nse_test", DAY, "a.csv")
    assert store.latest("nse_test", DAY, "a.csv") is None
    assert store.path_for("nse_test", DAY, "a.csv") == (
        tmp_path / "raw" / "nse_test" / "2026-11-09" / "a.csv"
    )
    assert not (tmp_path / "raw").exists()  # path_for/exists/latest create nothing

    rec = _save(store, "a.csv", b"x")
    assert store.exists("nse_test", DAY, "a.csv")
    assert store.latest("nse_test", DAY, "a.csv") == rec.path
    # other names, days and sources are independent
    assert not store.exists("nse_test", DAY, "b.csv")
    assert not store.exists("nse_test", date(2026, 11, 10), "a.csv")
    assert not store.exists("other", DAY, "a.csv")


def test_iter_manifest_in_order_and_empty_when_missing(tmp_path: Path) -> None:
    store = RawStore(tmp_path)
    assert list(store.iter_manifest()) == []
    _save(store, "a.csv", b"1")
    _save(store, "b.csv", b"2")
    _save(store, "a.csv", b"3")
    assert [e["name"] for e in store.iter_manifest()] == ["a.csv", "b.csv", "a.v2.csv"]


def test_save_result_uses_fetch_result_fields(tmp_path: Path) -> None:
    store = RawStore(tmp_path)
    result = FetchResult(
        url="https://example.test/final",
        status_code=200,
        content=b"body",
        headers={},
        fetched_at=FETCHED,
    )
    rec = store.save_result(source="nse_test", day=DAY, name="r.csv", result=result)
    assert rec.url == "https://example.test/final"
    assert rec.status_code == 200
    assert rec.fetched_at == FETCHED
    assert rec.path.read_bytes() == b"body"
