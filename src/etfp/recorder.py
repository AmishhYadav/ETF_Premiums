"""The intraday snapshot recorder: take one snapshot of every source, every 15 minutes.

Why this exists: nobody keeps an archive of an ETF's iNAV (the fund house's
running estimate of what one unit's holdings are worth). Every 15 minutes of
every trading session that we do not write down is lost for good. So this
module is built to be boring and hard to break:

* a "tick" is one snapshot of every source at one moment;
* each source runs on its own, so one broken website never stops the others;
* every raw answer is saved untouched (the raw store never overwrites);
* after every tick a small heartbeat file says what worked, so a watchdog (or
  `etfp status`) can see at a glance when the recorder has stopped;
* every failure is logged loudly and also appended to ``failures.jsonl``;
* the loop copes with a laptop that sleeps: it never sleeps for long in one
  go, and when it wakes up it writes down which ticks it missed.

State files live in ``data/state/``. They describe the recorder; they are not
raw data and may be rewritten.
"""

from __future__ import annotations

import fcntl
import functools
import json
import logging
import os
import tempfile
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from datetime import time as dtime
from pathlib import Path
from typing import Any

from etfp import calendars, paths
from etfp.collect import amc_inav
from etfp.config import CollectSettings, NseQuoteSettings, load_collect_settings, load_etfs
from etfp.http import PoliteClient, SourceError
from etfp.rawstore import RawRecord, RawStore
from etfp.timeutil import IST, now_ist, require_aware

logger = logging.getLogger("etfp.recorder")

HEARTBEAT_NAME = "inav_heartbeat.json"
FAILURES_NAME = "failures.jsonl"
LOCK_NAME = "inav.lock"

# Never sleep longer than this in one call. A laptop that sleeps in the middle of
# a long time.sleep() wakes up late and confused; short sleeps with a fresh look
# at the clock each time keep the loop honest.
MAX_SLEEP_S = 20.0

_MAX_ERROR_CHARS = 500

SourceFn = Callable[[PoliteClient, RawStore], RawRecord]
# An extra step does its own error handling and says what happened. It returns None when
# it chose not to run (switched off, or Playwright not installed): then it is not a source
# outcome at all, so it cannot raise a "source failed" alert.
StepFn = Callable[[RawStore], "SourceOutcome | None"]


class RecorderAlreadyRunningError(RuntimeError):
    """Another recorder holds the lock. The command line turns this into exit code 3."""


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class SourceOutcome:
    """What happened to one source in one tick."""

    source: str
    ok: bool
    relpath: str | None
    size_bytes: int | None
    error_type: str | None
    error: str | None
    duplicate: bool


@dataclass(frozen=True)
class TickResult:
    """One tick: when it ran, which scheduled slot it was for, and per-source outcomes."""

    started_at: datetime
    finished_at: datetime
    scheduled_for: dtime | None  # None for a manual one-off tick
    outcomes: tuple[SourceOutcome, ...]

    @property
    def ok_count(self) -> int:
        return sum(1 for o in self.outcomes if o.ok)

    @property
    def failed_count(self) -> int:
        return sum(1 for o in self.outcomes if not o.ok)


# ---------------------------------------------------------------------------
# Source registry
# ---------------------------------------------------------------------------
def _nse_etf_api(client: PoliteClient, store: RawStore) -> RawRecord:
    """NSE's all-ETF table. Imported lazily so this module still loads without it."""
    from etfp.collect import nse_etf_api

    return nse_etf_api.fetch_snapshot(client, store)


def default_sources() -> dict[str, SourceFn]:
    """name -> callable(client, store) -> RawRecord, in the order they run.

    To add a source later (for example a futures or FX quote snapshot), add one
    entry here.
    """
    sources: dict[str, SourceFn] = {"nse_etf_api": _nse_etf_api}
    for amc in amc_inav.AMCS:
        sources[amc_inav.source_name(amc)] = functools.partial(amc_inav.fetch_snapshot, amc=amc)
    return sources


NSE_QUOTE_STEP = "nse_quote"


def _nse_quote_step(settings: NseQuoteSettings | None) -> StepFn:
    """The browser-based NSE quote capture (order-book depth, D12) as an extra step.

    Runs AFTER the plain sources so a slow browser can never delay them. It never
    raises: whatever goes wrong becomes one warning line and, if nothing at all
    was saved, a failed outcome in the heartbeat.
    """

    def step(store: RawStore) -> SourceOutcome | None:
        from etfp.collect import nse_quote

        cfg = settings if settings is not None else load_collect_settings().nse_quote
        if cfg.enabled is False:
            return None
        if not nse_quote.playwright_available():
            if cfg.enabled == "auto":
                logger.warning(
                    "nse_quote skipped: Playwright is not installed (no order-book depth this "
                    "tick). Install: `uv sync --extra inav` then `uv run playwright install "
                    "chromium`; or set nse_quote.enabled: false in config/collect.yaml"
                )
                return None
            raise nse_quote.BrowserUnavailableError(
                "nse_quote.enabled is true but Playwright is not installed"
            )
        symbols = [e.symbol for e in load_etfs().tracked()]
        report = nse_quote.run_tick_capture(store, symbols, cfg)
        if report.problems:
            logger.warning("nse_quote: incomplete capture: %s", "; ".join(report.problems))
        first = report.saved[0]
        return SourceOutcome(
            NSE_QUOTE_STEP,
            True,
            first.relpath,
            sum(r.size_bytes for r in report.saved),
            None,
            None,
            all(r.duplicate for r in report.saved),
        )

    return step


def default_extra_steps(settings: CollectSettings | None = None) -> dict[str, StepFn]:
    """Extra steps run after the plain sources. Today: the browser quote capture."""
    return {NSE_QUOTE_STEP: _nse_quote_step(settings.nse_quote if settings else None)}


# ---------------------------------------------------------------------------
# State files
# ---------------------------------------------------------------------------
def _state(state_dir: Path | None) -> Path:
    return Path(state_dir) if state_dir is not None else paths.state_dir()


def _iso(ts: datetime) -> str:
    return require_aware(ts, "timestamp").astimezone(IST).isoformat()


def _hhmm(t: dtime | None) -> str | None:
    return t.strftime("%H:%M") if t is not None else None


def read_heartbeat(state_dir: Path | None = None) -> dict | None:
    """The last heartbeat, or None if there is none (or it cannot be read)."""
    path = _state(state_dir) / HEARTBEAT_NAME
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        logger.warning("heartbeat file %s is unreadable; ignoring it", path)
        return None
    return data if isinstance(data, dict) else None


def write_atomic(path: Path, payload: Any) -> None:
    """Write JSON to ``path`` so a reader never sees a half-written file.

    The text goes to a temporary file in the same folder first, then
    ``os.replace`` swaps it into place in one step. If anything fails, the
    temporary file is removed and the old file is left alone.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            text = json.dumps(payload, indent=2, ensure_ascii=False)
            fh.write(text + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def build_heartbeat(result: TickResult, previous: dict | None) -> dict[str, Any]:
    """Heartbeat contents for a finished tick.

    ``last_ok_at`` for a source is the start time of the last tick in which it
    worked. When this tick failed for a source, the old value is carried over, so
    the file always answers "when did this source last give us data?".
    """
    prev_sources = (previous or {}).get("sources", {})
    sources: dict[str, Any] = {}
    for o in result.outcomes:
        if o.ok:
            last_ok = _iso(result.started_at)
        else:
            old = prev_sources.get(o.source) if isinstance(prev_sources, dict) else None
            last_ok = old.get("last_ok_at") if isinstance(old, dict) else None
        sources[o.source] = {
            "ok": o.ok,
            "relpath": o.relpath,
            "error_type": o.error_type,
            "error": o.error,
            "last_ok_at": last_ok,
        }
    return {
        "last_tick_started": _iso(result.started_at),
        "last_tick_finished": _iso(result.finished_at),
        "scheduled_for": _hhmm(result.scheduled_for),
        "ok": result.ok_count,
        "failed": result.failed_count,
        "sources": sources,
    }


def append_failure(
    state_dir: Path | None,
    *,
    ts: datetime,
    source: str,
    error_type: str,
    error: str,
    scheduled_for: dtime | None,
) -> None:
    """Add one line to failures.jsonl (append-only log of everything that went wrong)."""
    folder = _state(state_dir)
    folder.mkdir(parents=True, exist_ok=True)
    entry = {
        "ts": _iso(ts),
        "source": source,
        "error_type": error_type,
        "error": error,
        "scheduled_for": _hhmm(scheduled_for),
    }
    line = (json.dumps(entry, ensure_ascii=False) + "\n").encode("utf-8")
    with (folder / FAILURES_NAME).open("ab") as fh:
        fh.write(line)  # one write call per line
        fh.flush()


def count_failures_on(day: date, state_dir: Path | None = None) -> int:
    """Number of failures.jsonl lines stamped with this IST date."""
    path = _state(state_dir) / FAILURES_NAME
    if not path.exists():
        return 0
    prefix = day.isoformat()
    count = 0
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            try:
                ts = json.loads(line).get("ts", "")
            except ValueError:
                continue
            if isinstance(ts, str) and ts.startswith(prefix):
                count += 1
    return count


# ---------------------------------------------------------------------------
# One tick
# ---------------------------------------------------------------------------
def _run_source(name: str, fn: SourceFn, client: PoliteClient, store: RawStore) -> SourceOutcome:
    """Run one source; any ordinary exception becomes a failed outcome, never a crash."""
    try:
        record = fn(client, store)
    except Exception as exc:  # deliberately broad: one source must never stop the others
        message = str(exc)[:_MAX_ERROR_CHARS]
        kind = type(exc).__name__
        # Expected trouble (SourceError and friends) already says everything in its
        # message; an unexpected error also gets a traceback so it can be fixed.
        logger.error(
            "source %s failed: %s: %s",
            name,
            kind,
            message,
            exc_info=not isinstance(exc, SourceError),
        )
        return SourceOutcome(name, False, None, None, kind, message, False)
    return SourceOutcome(
        name, True, record.relpath, record.size_bytes, None, None, bool(record.duplicate)
    )


def _run_step(name: str, fn: StepFn, store: RawStore) -> SourceOutcome | None:
    """Run one extra step. Whatever it does, the tick goes on."""
    try:
        return fn(store)
    except Exception as exc:  # deliberately broad: an optional step must never stop the tick
        message = str(exc)[:_MAX_ERROR_CHARS]
        kind = type(exc).__name__
        # One warning line per tick. Expected trouble (NSE refusing, browser missing) is
        # already clear from the message; an unexpected error also gets a traceback.
        expected = isinstance(exc, SourceError) or kind == "BrowserUnavailableError"
        logger.warning("extra step %s failed: %s: %s", name, kind, message, exc_info=not expected)
        return SourceOutcome(name, False, None, None, kind, message, False)


def _record_tick_state(result: TickResult, state_dir: Path | None) -> None:
    """Write the failure lines and heartbeat. Trouble here is logged, never raised."""
    try:
        for o in result.outcomes:
            if not o.ok:
                append_failure(
                    state_dir,
                    ts=result.finished_at,
                    source=o.source,
                    error_type=o.error_type or "Error",
                    error=o.error or "",
                    scheduled_for=result.scheduled_for,
                )
        previous = read_heartbeat(state_dir)
        write_atomic(_state(state_dir) / HEARTBEAT_NAME, build_heartbeat(result, previous))
    except Exception:
        logger.exception("could not write recorder state files in %s", _state(state_dir))


def _log_tick(result: TickResult) -> None:
    saved = sum(o.size_bytes or 0 for o in result.outcomes if o.ok and not o.duplicate)
    failed = [f"{o.source} ({o.error_type})" for o in result.outcomes if not o.ok]
    slot = _hhmm(result.scheduled_for) or "manual"
    text = f"tick {slot}: {result.ok_count} ok, {result.failed_count} failed, {saved} bytes saved"
    if failed:
        text += "; failed: " + ", ".join(failed)
    logger.info(text)


def run_tick(
    client: PoliteClient,
    store: RawStore,
    *,
    sources: Mapping[str, SourceFn] | None = None,
    extra_steps: Mapping[str, StepFn] | None = None,
    scheduled_for: dtime | None = None,
    state_dir: Path | None = None,
    now: Callable[[], datetime] = now_ist,
) -> TickResult:
    """Snapshot every source once, in order, then the extra steps, and update the heartbeat.

    Extra steps (the browser quote capture) only run by default when ``sources`` is
    also the default; a caller that passes its own ``sources`` gets exactly those
    unless it passes ``extra_steps`` too. That keeps tests from starting a browser.
    """
    registry = default_sources() if sources is None else sources
    if extra_steps is None:
        extra_steps = default_extra_steps() if sources is None else {}
    started = now()
    results = [_run_source(name, fn, client, store) for name, fn in registry.items()]
    for name, step in extra_steps.items():
        outcome = _run_step(name, step, store)
        if outcome is not None:
            results.append(outcome)
    outcomes = tuple(results)
    result = TickResult(
        started_at=started, finished_at=now(), scheduled_for=scheduled_for, outcomes=outcomes
    )
    _record_tick_state(result, state_dir)
    _log_tick(result)
    return result


# ---------------------------------------------------------------------------
# Single-instance lock
# ---------------------------------------------------------------------------
@contextmanager
def single_instance_lock(state_dir: Path | None = None) -> Iterator[None]:
    """Hold an exclusive lock for the life of the block, so two recorders cannot run at once.

    Uses ``flock`` on ``inav.lock``. The operating system drops the lock when the
    process ends, even if it crashes, so a stale lock file cannot block us.
    """
    folder = _state(state_dir)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / LOCK_NAME
    handle = path.open("a+")
    try:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            logger.error("another recorder already holds %s; not starting", path)
            raise RecorderAlreadyRunningError(
                f"another iNAV recorder is already running (lock held: {path})"
            ) from None
        handle.seek(0)
        handle.truncate()
        handle.write(f"{os.getpid()}\n")
        handle.flush()
        yield
    finally:
        handle.close()  # closing releases the flock


# ---------------------------------------------------------------------------
# The loop
# ---------------------------------------------------------------------------
def _schedule_for(day: date, settings: CollectSettings) -> list[datetime]:
    return [datetime.combine(day, t, tzinfo=IST) for t in settings.inav_tick_times()]


def _first_slot(schedule: list[datetime], t: datetime, every: timedelta) -> int | None:
    """Index of the first slot to handle when the recorder starts at time ``t``.

    Before the first slot: wait for it. In the middle of the session: if the
    most recent slot is less than ``every`` ago, take it now (we are only a
    little late); older slots are not "missed", they happened before we
    started. After the last slot: nothing to do (None).
    """
    if t > schedule[-1]:
        return None
    past = [i for i, s in enumerate(schedule) if s <= t]
    if past and t - schedule[past[-1]] < every:
        return past[-1]
    future = [i for i, s in enumerate(schedule) if s > t]
    return future[0] if future else None


def _wait_until(
    target: datetime, now: Callable[[], datetime], sleep: Callable[[float], None]
) -> None:
    """Sleep until ``target`` in short chunks, re-reading the clock each time."""
    while True:
        remaining = (target - now()).total_seconds()
        if remaining <= 0:
            return
        sleep(min(remaining, MAX_SLEEP_S))


def run_loop(
    *,
    client: PoliteClient,
    store: RawStore,
    settings: CollectSettings,
    state_dir: Path | None = None,
    force: bool = False,
    now: Callable[[], datetime] = now_ist,
    sleep: Callable[[float], None] = time.sleep,
    sources: Mapping[str, SourceFn] | None = None,
    extra_steps: Mapping[str, StepFn] | None = None,
) -> list[TickResult]:
    """Run today's whole schedule: a tick at each configured time, then return.

    Returns [] without any request when the exchange is closed today (unless
    ``force``) or when started after the last tick time.
    """
    today = now().astimezone(IST).date()
    if not force and not calendars.is_nse_session(today):
        logger.info("NSE is closed on %s; the recorder has nothing to do", today.isoformat())
        return []
    if calendars.is_provisional("IN", today):
        logger.warning(
            "the NSE holiday calendar for %d is a guess (every weekday is treated as a trading "
            "day); check config/calendars/nse_holidays.yaml",
            today.year,
        )

    if extra_steps is None and sources is None:
        extra_steps = default_extra_steps(settings)
    schedule = _schedule_for(today, settings)
    every = timedelta(minutes=settings.inav.every_minutes)
    results: list[TickResult] = []

    with single_instance_lock(state_dir):
        first = _first_slot(schedule, now(), every)
        if first is None:
            logger.info("all of today's tick times have passed; nothing to record")
            return []
        logger.info(
            "recorder started for %s: %d tick times, first to handle is %s",
            today.isoformat(),
            len(schedule) - first,
            schedule[first].strftime("%H:%M"),
        )
        for slot in schedule[first:]:
            _wait_until(slot, now, sleep)
            late = now() - slot
            if late >= every:
                _record_missed(slot, late, state_dir, now)
                continue
            results.append(
                run_tick(
                    client,
                    store,
                    sources=sources,
                    extra_steps=extra_steps,
                    scheduled_for=slot.time(),
                    state_dir=state_dir,
                    now=now,
                )
            )
    return results


def _record_missed(
    slot: datetime, late: timedelta, state_dir: Path | None, now: Callable[[], datetime]
) -> None:
    """A slot was reached too late (the machine was probably asleep). Write it down."""
    minutes = int(late.total_seconds() // 60)
    message = f"tick {slot.strftime('%H:%M')} was not run: reached {minutes} minutes late"
    logger.warning("MissedTick: %s", message)
    try:
        append_failure(
            state_dir,
            ts=now(),
            source="_tick",
            error_type="MissedTick",
            error=message,
            scheduled_for=slot.time(),
        )
    except Exception:
        logger.exception("could not record the missed tick in failures.jsonl")
