"""Tests for ``run_alerts``: dedupe, dry run, delivery failure, message batching.

Telegram is a MockTransport; the clean store is a stand-in holding small frames.
"""

from __future__ import annotations

import json
from datetime import date

import httpx
import pandas as pd
import pytest

from etfp.alerts import AlertDeliveryError, MissingCredentialsError, run_alerts
from etfp.alerts.rules import Alert
from etfp.alerts.runner import (
    DISCLAIMER_SHORT,
    STATE_FILE,
    build_batches,
    load_sent,
    save_sent,
)
from etfp.alerts.telegram import Credentials, TelegramSender
from test_alerts_helpers import D_LAST, D_PREV, FakeStore, etf_day, ist, only

TOKEN = "999:RUN-SECRET"
NOW = ist(D_LAST, 22, 0)
CFG = only("premium_cross")


def store_with_cross() -> FakeStore:
    # MONQ50 20% -> 55% crosses 25% and 50% on 13 Nov: exactly one alert.
    return FakeStore(
        etf_day=etf_day(
            [
                {"symbol": "MONQ50", "date": D_PREV, "prem_fv": 0.20},
                {"symbol": "MONQ50", "date": D_LAST, "prem_fv": 0.55},
            ]
        )
    )


class Telegram:
    """Records posts; ``fail_first`` makes the first N answer with an error."""

    def __init__(self, fail_first: int = 0) -> None:
        self.posts: list[str] = []
        self.fail_first = fail_first
        self.attempts = 0

    def sender(self) -> TelegramSender:
        def handler(request: httpx.Request) -> httpx.Response:
            self.attempts += 1
            if self.attempts <= self.fail_first:
                return httpx.Response(500, json={"ok": False, "description": f"oops {TOKEN}"})
            self.posts.append(json.loads(request.content)["text"])
            return httpx.Response(200, json={"ok": True})

        client = httpx.Client(transport=httpx.MockTransport(handler))
        return TelegramSender(Credentials(token=TOKEN, chat_id="1"), client=client)


def run(store, tmp_path, tg: Telegram | None = None, *, dry_run=False, cfg=CFG, out=None):
    return run_alerts(
        store,
        dry_run=dry_run,
        now=NOW,
        config=cfg,
        collect=None,
        state_dir=tmp_path,
        sender=tg.sender() if tg else None,
        out=out or (lambda s: None),
        pause_s=0,
    )


def test_send_then_second_run_sends_nothing(tmp_path) -> None:
    tg = Telegram()
    first = run(store_with_cross(), tmp_path, tg)
    assert [a.key for a in first] == ["premium_cross|MONQ50|2026-11-13|up"]
    assert len(tg.posts) == 1
    assert "rose above 25% and 50%" in tg.posts[0] and DISCLAIMER_SHORT in tg.posts[0]
    saved = json.loads((tmp_path / STATE_FILE).read_text())
    assert list(saved["sent"]) == ["premium_cross|MONQ50|2026-11-13|up"]

    again = run(store_with_cross(), tmp_path, tg)
    assert again == [] and len(tg.posts) == 1  # same alert, same day: not re-sent


def test_next_day_data_with_same_situation_is_a_new_alert(tmp_path) -> None:
    tg = Telegram()
    run(store_with_cross(), tmp_path, tg)
    later = etf_day(
        [
            {"symbol": "MONQ50", "date": D_LAST, "prem_fv": 0.40},
            {"symbol": "MONQ50", "date": date(2026, 11, 16), "prem_fv": 0.60},
        ]
    )
    run_alerts(
        FakeStore(etf_day=later), dry_run=False, now=ist(date(2026, 11, 16), 22), config=CFG,
        collect=None, state_dir=tmp_path, sender=tg.sender(), out=lambda s: None, pause_s=0,
    )  # fmt: skip
    assert len(tg.posts) == 2 and "2026-11-16" in tg.posts[1]


def test_dry_run_prints_sends_nothing_and_writes_nothing(tmp_path) -> None:
    printed: list[str] = []
    out = run(store_with_cross(), tmp_path, None, dry_run=True, out=printed.append)
    assert len(out) == 1
    text = "\n".join(printed)
    assert "[dry run]" in text and "nothing sent" in text and "MONQ50" in text
    assert not (tmp_path / STATE_FILE).exists()
    assert not (tmp_path / "alerts.lock").exists()  # a dry run never needs the lock


def test_dry_run_respects_already_sent(tmp_path) -> None:
    run(store_with_cross(), tmp_path, Telegram())
    printed: list[str] = []
    assert run(store_with_cross(), tmp_path, None, dry_run=True, out=printed.append) == []
    assert printed == ["[dry run] no new alerts"]


def test_failed_send_raises_keeps_state_empty_and_retries_next_time(tmp_path) -> None:
    tg = Telegram(fail_first=1)
    with pytest.raises(AlertDeliveryError) as err:
        run(store_with_cross(), tmp_path, tg)
    assert len(err.value.failed) == 1 and err.value.sent == []
    assert TOKEN not in str(err.value) and "HTTP 500" in str(err.value)
    assert not (tmp_path / STATE_FILE).exists()
    retry = run(store_with_cross(), tmp_path, tg)  # Telegram works now
    assert len(retry) == 1 and len(tg.posts) == 1


def test_partial_delivery_remembers_only_what_went_out(tmp_path, monkeypatch) -> None:
    # A tiny size budget puts each alert in its own message; the second message fails.
    monkeypatch.setattr("etfp.alerts.runner._BATCH_BUDGET", 10)
    store = FakeStore(
        etf_day=etf_day(
            [
                {"symbol": s, "date": d, "prem_fv": p}
                for s in ("A", "B")
                for d, p in ((D_PREV, 0.2), (D_LAST, 0.6))
            ]
        )
    )
    posts: list[str] = []
    attempts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        if len(attempts) == 2:
            return httpx.Response(429, json={"ok": False, "description": "Too Many Requests"})
        posts.append(json.loads(request.content)["text"])
        return httpx.Response(200, json={"ok": True})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    sender = TelegramSender(Credentials(token=TOKEN, chat_id="1"), client=client)
    with pytest.raises(AlertDeliveryError) as err:
        run_alerts(
            store, dry_run=False, now=NOW, config=CFG, collect=None, state_dir=tmp_path,
            sender=sender, out=lambda s: None, pause_s=0,
        )  # fmt: skip
    assert [a.symbol for a in err.value.sent] == ["A"]
    assert [a.symbol for a in err.value.failed] == ["B"]
    assert list(load_sent(tmp_path / STATE_FILE)) == ["premium_cross|A|2026-11-13|up"]
    assert len(posts) == 1 and "A:" in posts[0]


def test_missing_credentials_fail_before_anything_else(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.setattr("etfp.paths.project_root", lambda: tmp_path)  # no .env here
    with pytest.raises(MissingCredentialsError):
        run_alerts(store_with_cross(), dry_run=False, now=NOW, config=CFG, collect=None,
                   state_dir=tmp_path, out=lambda s: None)  # fmt: skip
    assert not (tmp_path / STATE_FILE).exists()


def test_empty_store_gives_a_data_alert_not_a_crash(tmp_path) -> None:
    out = run(FakeStore(), tmp_path, Telegram(), cfg=only("data_failure"))
    assert [a.rule for a in out] == ["data_stale"]


def test_tracked_filter_comes_from_etf_master(tmp_path) -> None:
    master = pd.DataFrame({"symbol": ["MONQ50", "OTHER"], "tracked": [False, True]})
    store = store_with_cross()
    store.tables["etf_master"] = master
    assert run(store, tmp_path, Telegram()) == []  # MONQ50 exists in etf_day but is not tracked


def test_corrupt_state_file_is_loud(tmp_path) -> None:
    (tmp_path / STATE_FILE).write_text("{not json")
    with pytest.raises(RuntimeError, match="unreadable"):
        run(store_with_cross(), tmp_path, Telegram())


def test_state_prunes_old_keys_and_keeps_recent(tmp_path) -> None:
    path = tmp_path / STATE_FILE
    save_sent(
        path,
        {"r|A|2026-10-01|up": "x", "r|A|2026-11-12|up": "y", "weird": "z"},
        today=D_LAST,
        keep_days=14,
    )
    assert sorted(load_sent(path)) == ["r|A|2026-11-12|up", "weird"]


def test_batches_split_under_limit_and_end_with_disclaimer() -> None:
    alerts = [
        Alert("r", f"S{i}", D_LAST, f"r|S{i}|2026-11-13", f"S{i}: " + "w" * 900) for i in range(10)
    ]
    batches = build_batches(alerts, NOW)
    assert len(batches) > 1
    assert sum(len(b.alerts) for b in batches) == 10
    for b in batches:
        assert len(b.text) <= 4096 and b.text.endswith(DISCLAIMER_SHORT)
        assert b.text.startswith("ETF premium tracker: 13 Nov 2026, 22:00 IST")
