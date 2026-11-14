"""Run the alerts: read data, evaluate the rules, drop repeats, send, remember what was sent.

``run_alerts(store, dry_run=...)`` is the one function the command line calls (``etfp alerts``).

Not sending the same alert twice
--------------------------------
Every alert has a ``key`` (rule, symbol, data day, direction or level).
``data/state/alerts_sent.json`` lists the keys already sent. A key is written only AFTER
Telegram accepted the message, so a failed send is tried again on the next run instead of
being lost. Dry runs never write the file.
Keys older than ``dedupe_keep_days`` are dropped so the file stays small.

Two runs at the same moment (for example a slow cron job overlapping the next one) would both
see "not sent yet" and both send. A lock file stops that: the second run exits with an error.
"""

from __future__ import annotations

import fcntl
import json
import logging
import time as time_mod
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Protocol

import pandas as pd

from etfp import paths
from etfp.alerts.rules import (
    DISCLAIMER_SHORT,
    Alert,
    AlertConfig,
    evaluate,
    load_alert_config,
)
from etfp.alerts.telegram import (
    MAX_MESSAGE_CHARS,
    TelegramError,
    TelegramSender,
    load_credentials,
)
from etfp.config import CollectSettings, load_collect_settings
from etfp.recorder import read_heartbeat, write_atomic
from etfp.timeutil import IST, now_ist, require_aware

logger = logging.getLogger("etfp.alerts")

STATE_FILE = "alerts_sent.json"
LOCK_FILE = "alerts.lock"
STATE_VERSION = 1
_BATCH_BUDGET = MAX_MESSAGE_CHARS - 600  # room for the header and the disclaimer line


class AlertsAlreadyRunningError(RuntimeError):
    """Another ``etfp alerts`` run holds the lock."""


class AlertDeliveryError(RuntimeError):
    """Some alerts could not be delivered. ``sent`` went out and is remembered; ``failed`` will
    be tried again on the next run."""

    def __init__(self, message: str, *, sent: list[Alert], failed: list[Alert]) -> None:
        super().__init__(message)
        self.sent = sent
        self.failed = failed


class TableSource(Protocol):
    """The part of ``etfp.store.Store`` this module uses (lets tests pass a stand-in)."""

    def has_table(self, table: str) -> bool: ...

    def read_table(self, name: str) -> pd.DataFrame: ...


# --------------------------------------------------------------------------
# Remembering what was sent
# --------------------------------------------------------------------------
def _key_day(key: str) -> date | None:
    parts = key.split("|")
    try:
        return date.fromisoformat(parts[2])
    except (IndexError, ValueError):
        return None


def load_sent(path: Path) -> dict[str, str]:
    """Keys already sent (key -> ISO time sent). A missing file is empty; a corrupt one is an
    error, because guessing "nothing sent yet" would re-send everything."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        raise RuntimeError(
            f"{path} is unreadable ({type(exc).__name__}). Fix or delete it; deleting means "
            "today's alerts may be sent again."
        ) from exc
    sent = data.get("sent") if isinstance(data, dict) else None
    if not isinstance(sent, dict):
        raise RuntimeError(f"{path} has an unexpected shape; fix or delete it.")
    return {str(k): str(v) for k, v in sent.items()}


def save_sent(path: Path, sent: dict[str, str], *, today: date, keep_days: int) -> None:
    cutoff = today - timedelta(days=keep_days)
    kept = {k: v for k, v in sent.items() if (_key_day(k) or today) >= cutoff}
    write_atomic(path, {"version": STATE_VERSION, "sent": dict(sorted(kept.items()))})


@contextmanager
def _exclusive(lock_path: Path) -> Iterator[None]:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("w") as fh:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise AlertsAlreadyRunningError(
                f"another alerts run is in progress (lock {lock_path})"
            ) from None
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


# --------------------------------------------------------------------------
# Message text
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Batch:
    """One Telegram message and the alerts it carries."""

    text: str
    alerts: tuple[Alert, ...]


def build_batches(alerts: list[Alert], now: datetime) -> list[Batch]:
    """Group alerts into messages under Telegram's size limit. Every message ends with the
    short disclaimer. Alerts keep their order."""
    local = require_aware(now, "now").astimezone(IST)
    header = f"ETF premium tracker: {local:%d %b %Y, %H:%M} IST"
    groups: list[list[Alert]] = []
    size = 0
    for alert in alerts:
        line = f"- {alert.message}"
        if groups and size + len(line) + 2 <= _BATCH_BUDGET:
            groups[-1].append(alert)
            size += len(line) + 2
        else:
            groups.append([alert])
            size = len(line) + 2
    batches = []
    for group in groups:
        body = "\n\n".join(f"- {a.message}" for a in group)
        batches.append(Batch(f"{header}\n\n{body}\n\n{DISCLAIMER_SHORT}", tuple(group)))
    return batches


# --------------------------------------------------------------------------
# The run
# --------------------------------------------------------------------------
def _read_optional(store: TableSource, table: str) -> pd.DataFrame | None:
    if not store.has_table(table):
        return None
    return store.read_table(table)


def _tracked_symbols(master: pd.DataFrame | None) -> set[str] | None:
    """Symbols marked ``tracked`` in etf_master; None (= all) when there is no master table."""
    if master is None or "tracked" not in master.columns or "symbol" not in master.columns:
        return None
    flags = master["tracked"].fillna(False).astype(bool)
    return {str(s) for s in master.loc[flags, "symbol"]}


def run_alerts(
    store: TableSource,
    *,
    dry_run: bool,
    now: datetime | None = None,
    config: AlertConfig | None = None,
    collect: CollectSettings | None = None,
    state_dir: Path | None = None,
    sender: TelegramSender | None = None,
    out: Callable[[str], None] = print,
    pause_s: float = 1.0,
) -> list[Alert]:
    """Evaluate the rules and send the alerts that have not been sent yet.

    Returns the new alerts: the ones sent, or with ``dry_run`` the ones that WOULD be sent
    (printed through ``out``; nothing is sent and the sent-file is not touched).

    Raises ``MissingCredentialsError`` (before doing anything) when the Telegram token or chat
    id is missing and this is not a dry run, and ``AlertDeliveryError`` if Telegram rejects or
    cannot be reached (alerts that did go out are remembered, the rest will be retried).
    """
    when = require_aware(now, "now") if now is not None else now_ist()
    cfg = config or load_alert_config()
    state = Path(state_dir) if state_dir is not None else paths.state_dir()
    if collect is None:
        try:
            collect = load_collect_settings()
        except FileNotFoundError:
            collect = None

    own_sender = None
    if not dry_run and sender is None:
        own_sender = sender = TelegramSender(load_credentials())  # fails early if unset
    try:
        with _exclusive(state / LOCK_FILE) if not dry_run else _no_lock():
            return _run_locked(store, cfg, collect, when, state, dry_run, sender, out, pause_s)
    finally:
        if own_sender is not None:
            own_sender.close()


@contextmanager
def _no_lock() -> Iterator[None]:
    yield


def _run_locked(
    store: TableSource,
    cfg: AlertConfig,
    collect: CollectSettings | None,
    when: datetime,
    state: Path,
    dry_run: bool,
    sender: TelegramSender | None,
    out: Callable[[str], None],
    pause_s: float,
) -> list[Alert]:
    etf_day = _read_optional(store, "etf_day")
    master = _read_optional(store, "etf_master")
    snapshots = _read_optional(store, "inav_snapshots")
    tracked = _tracked_symbols(master)
    if tracked is not None and not tracked:
        logger.warning("etf_master has no tracked ETFs; premium and band rules will find nothing")

    alerts = evaluate(
        etf_day,
        config=cfg,
        now=when,
        tracked=tracked,
        snapshots=snapshots,
        heartbeat=read_heartbeat(state),
        collect=collect,
    )
    sent_path = state / STATE_FILE
    sent = load_sent(sent_path)
    new = [a for a in alerts if a.key not in sent]
    logger.info("%d alert(s) true, %d new", len(alerts), len(new))
    if not new:
        if dry_run:
            out("[dry run] no new alerts")
        return []

    batches = build_batches(new, when)
    if dry_run:
        out(
            f"[dry run] {len(new)} alert(s) would be sent in {len(batches)} message(s); "
            "nothing sent"
        )
        for batch in batches:
            out(batch.text)
            out("")
        return new

    assert sender is not None
    delivered: list[Alert] = []
    for i, batch in enumerate(batches):
        if i and pause_s > 0:
            time_mod.sleep(pause_s)  # Telegram allows about one message per second per chat
        try:
            sender.send(batch.text)
        except TelegramError as exc:
            failed = [a for b in batches[i:] for a in b.alerts]
            raise AlertDeliveryError(
                f"{len(failed)} alert(s) not delivered, {len(delivered)} delivered: {exc}",
                sent=delivered,
                failed=failed,
            ) from None
        delivered.extend(batch.alerts)
        stamp = when.astimezone(IST).isoformat()
        for alert in batch.alerts:
            sent[alert.key] = stamp
        # Written after every message: a crash later cannot make us re-send this one.
        save_sent(
            sent_path, sent, today=when.astimezone(IST).date(), keep_days=cfg.dedupe_keep_days
        )
    return delivered
