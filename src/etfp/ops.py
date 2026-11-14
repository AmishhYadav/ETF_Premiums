"""Operations helpers: the macOS scheduler jobs, the daily run, and the `etfp status` report.

Why this exists: nothing helps if it does not start by itself every trading day. On a
Mac the built-in scheduler is `launchd`. This module only *writes the text* of the job
descriptions (plists); installing them is a manual step for the owner (see
docs/OPERATIONS.md), so nothing here changes the machine.

It also holds the logic of ``etfp run daily``: the order of the steps, the lock that stops
two runs overlapping, and the JSON run record that ``etfp status`` shows. The real work of
each step (download, build, export, alerts) is passed in as a function, so the order and the
failure rules can be tested without a network or a build.
"""

from __future__ import annotations

import json
import os
import plistlib
import shutil
import sys
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from etfp import calendars, paths
from etfp.rawstore import RawStore
from etfp.recorder import count_failures_on, read_heartbeat, write_atomic
from etfp.timeutil import IST, now_ist, require_aware

DEFAULT_LABEL = "com.etfp.inav"
CAFFEINATE = "/usr/bin/caffeinate"
BASE_PATH = ("/usr/local/bin", "/opt/homebrew/bin", "/usr/bin", "/bin")

# launchd runs the job in the Mac's LOCAL time zone. 09:10 is five minutes
# before NSE opens at 09:15 India time, so the Mac must be set to India time.
START_HOUR = 9
START_MINUTE = 10
WEEKDAYS = (1, 2, 3, 4, 5)  # launchd: 1 = Monday ... 5 = Friday

# Daily pipeline schedule (India time, Mon-Fri).
#  * Evening 19:45: NSE posts the day's files from about 19:00 (docs/DECISIONS.md D22), so
#    19:45 leaves a margin; AMFI has the Indian NAVs by then.
#  * Morning 10:35: overseas funds may publish the previous day's NAV until 10:00 the next
#    business day (D10), so 10:35 leaves a 35 minute margin.
#  Mon-Fri (not Tue-Sat) for the morning run because Monday's run is the one that fetches
#  Friday's US NAV. Tue-Sat would deliver Friday's NAV only on Saturday.
EVENING_HOUR, EVENING_MINUTE = 19, 45
MORNING_HOUR, MORNING_MINUTE = 10, 35

JOBS = ("inav", "daily-evening", "daily-morning")
MUHURAT_NOTE = (
    "Note: this job runs Monday to Friday only. A Diwali Muhurat session that falls on a "
    "weekend day (Sunday 8 Nov 2026, D24 in docs/DECISIONS.md) is NOT started by this plist. "
    "The next morning run collects that session's NSE files and the late NAVs by itself; "
    "for the same-day snapshots (NSE ETF list, AMFI NAVAll) run by hand on the Muhurat "
    "evening: etfp run daily --phase evening"
)

Phase = Literal["evening", "morning"]
PHASES: tuple[Phase, ...] = ("evening", "morning")


def find_uv() -> Path:
    """Locate the `uv` program, or say clearly that it is missing."""
    found = shutil.which("uv")
    if found is None:
        raise FileNotFoundError(
            "could not find 'uv' on PATH; install it (https://docs.astral.sh/uv/) "
            "or pass its full path"
        )
    return Path(found)


def _render_plist(
    *,
    label: str,
    project_root: Path,
    uv_path: Path,
    etfp_args: list[str],
    intervals: list[dict[str, int]],
    log_stem: str,
) -> str:
    """Shared plist text. ``caffeinate -i`` stops the Mac from going to sleep because it
    looks idle while the job runs."""
    root = Path(project_root)
    uv = Path(uv_path)
    path_dirs = list(BASE_PATH)
    if str(uv.parent) not in path_dirs:
        path_dirs.append(str(uv.parent))
    plist = {
        "Label": label,
        "ProgramArguments": [
            CAFFEINATE,
            "-i",
            str(uv),
            "run",
            "--project",
            str(root),
            "etfp",
            *etfp_args,
        ],
        "WorkingDirectory": str(root),
        "StartCalendarInterval": intervals,
        "StandardOutPath": str(root / "logs" / f"launchd.{log_stem}.out.log"),
        "StandardErrorPath": str(root / "logs" / f"launchd.{log_stem}.err.log"),
        "EnvironmentVariables": {"ETFP_ROOT": str(root), "PATH": ":".join(path_dirs)},
        "RunAtLoad": False,
        "ProcessType": "Background",
    }
    return plistlib.dumps(plist, sort_keys=False).decode("utf-8")


def render_inav_plist(*, project_root: Path, uv_path: Path, label: str = DEFAULT_LABEL) -> str:
    """Text of a launchd LaunchAgent that starts the recorder at 09:10, Monday to Friday.

    On weekday market holidays the job still starts; the recorder checks the exchange
    calendar and exits by itself.
    """
    return _render_plist(
        label=label,
        project_root=project_root,
        uv_path=uv_path,
        etfp_args=["collect", "inav", "--loop"],
        intervals=[{"Weekday": d, "Hour": START_HOUR, "Minute": START_MINUTE} for d in WEEKDAYS],
        log_stem="inav",
    )


def render_daily_plist(
    *, project_root: Path, uv_path: Path, phase: Phase, label: str | None = None
) -> str:
    """Text of a launchd LaunchAgent for ``etfp run daily --phase <phase>``.

    Evening 19:45 and morning 10:35 India time, Monday to Friday. On a weekday market
    holiday the job still starts and ``run daily`` exits 0 by itself ("NSE closed").
    If the Mac is asleep at that time, launchd runs the job when it wakes up.
    """
    if phase not in PHASES:
        raise ValueError(f"phase must be one of {PHASES}, got {phase!r}")
    hour, minute = (
        (EVENING_HOUR, EVENING_MINUTE) if phase == "evening" else (MORNING_HOUR, MORNING_MINUTE)
    )
    return _render_plist(
        label=label or f"com.etfp.daily-{phase}",
        project_root=project_root,
        uv_path=uv_path,
        etfp_args=["run", "daily", "--phase", phase],
        intervals=[{"Weekday": d, "Hour": hour, "Minute": minute} for d in WEEKDAYS],
        log_stem=f"daily-{phase}",
    )


def render_plist(job: str, *, project_root: Path, uv_path: Path) -> str:
    """Plist text for one of ``JOBS``."""
    if job == "inav":
        return render_inav_plist(project_root=project_root, uv_path=uv_path)
    if job == "daily-evening":
        return render_daily_plist(project_root=project_root, uv_path=uv_path, phase="evening")
    if job == "daily-morning":
        return render_daily_plist(project_root=project_root, uv_path=uv_path, phase="morning")
    raise ValueError(f"unknown job {job!r}; choose from: {', '.join(JOBS)}")


# ---------------------------------------------------------------------------
# `etfp run daily`: choosing the day, the lock, the steps, the run record
# ---------------------------------------------------------------------------
EVENING_FROM_HOUR = 18  # without --phase, a run at or after 18:00 IST is the evening run
RUNS_DIR = "runs"
LOCK_NAME = "run_daily.lock"
STALE_LOCK = timedelta(hours=2)
RECORD_VERSION = 1

StepStatus = Literal["ok", "warning", "failed", "skipped"]


def default_phase(now: datetime) -> Phase:
    """Evening from 18:00 India time, morning before that."""
    return "evening" if now.astimezone(IST).hour >= EVENING_FROM_HOUR else "morning"


def resolve_run_day(phase: Phase, requested: date | None, now: datetime) -> tuple[date, str | None]:
    """Which trading day a run processes, and a message if there is nothing to do.

    ``--date`` always means "the trading day being processed".
      * evening: that day is today (India date) unless ``--date`` says otherwise.
      * morning: that day is the LAST SESSION BEFORE today. The morning run finishes the
        previous session's data: US-linked funds publish that session's NAV after 09:00
        (docs/DECISIONS.md D10). On Monday that session is Friday (or a Sunday Muhurat
        session, D24).
    Returns ``(day, None)`` to go ahead, or ``(day, message)`` when ``day`` is not an NSE
    session (weekend, holiday). Muhurat sessions count as sessions through the calendar.
    Raises ValueError for a future date or a date outside the calendar.
    """
    today = require_aware(now, "now").astimezone(IST).date()
    if requested is not None:
        if requested > today:
            raise ValueError(f"--date {requested.isoformat()} is in the future (today is {today})")
        day = requested
    elif phase == "evening":
        day = today
    else:
        day = calendars.previous_session("IN", today)
    if not calendars.is_nse_session(day):
        return day, (
            f"NSE was closed on {day.isoformat()} ({day.strftime('%A')}); "
            "nothing to collect or build for that day."
        )
    return day, None


# -- lock --------------------------------------------------------------------------


class RunLockedError(RuntimeError):
    """Another ``etfp run daily`` is (probably) still running."""


@dataclass
class LockResult:
    """``taken_over`` says why an old lock was replaced; None if there was none."""

    taken_over: str | None = None


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)  # signal 0 = "does this process exist?", sends nothing
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, owned by someone else
    return True


def _read_lock(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _lock_verdict(path: Path, now: datetime, pid_alive: Callable[[int], bool]) -> tuple[bool, str]:
    """(is_stale, description) for an existing lock file."""
    info = _read_lock(path)
    started: datetime | None = None
    if info is not None:
        try:
            started = datetime.fromisoformat(str(info.get("started_at")))
        except ValueError:
            started = None
    if started is None or started.tzinfo is None:
        try:  # unreadable lock: judge by the file's own age
            started = datetime.fromtimestamp(path.stat().st_mtime, tz=IST)
        except OSError:
            return True, f"lock {path} vanished while being read"
    age = now - started
    who = ""
    if info is not None:
        who = f" (pid {info.get('pid')}, {info.get('phase')} {info.get('day')})"
    where = f"started {started.astimezone(IST).isoformat(timespec='seconds')}{who}"
    if age >= STALE_LOCK:
        hours = age.total_seconds() / 3600
        return True, f"stale lock taken over: {where} is {hours:.1f} h old (limit 2 h)"
    pid = info.get("pid") if info is not None else None
    if isinstance(pid, int) and not pid_alive(pid):
        return True, f"stale lock taken over: {where}, but that process no longer exists"
    return False, f"another `etfp run daily` is running: {where}"


@contextmanager
def run_lock(
    state_dir: Path,
    *,
    now: datetime,
    phase: str,
    day: date,
    pid: int | None = None,
    pid_alive: Callable[[int], bool] = _pid_alive,
) -> Iterator[LockResult]:
    """Hold ``data/state/run_daily.lock`` for the block, so two runs cannot overlap.

    The file is created with "fail if it exists" (atomic), and holds who started it and
    when. An existing lock is respected for up to 2 hours; after that, or when its process
    is gone (a crash), it is reported and replaced. A run never deletes a lock that is not
    its own (it checks a random token), so a late finisher cannot free someone else's lock.
    """
    require_aware(now, "now")
    path = Path(state_dir) / LOCK_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex
    payload = json.dumps(
        {
            "pid": os.getpid() if pid is None else pid,
            "started_at": now.astimezone(IST).isoformat(timespec="seconds"),
            "phase": phase,
            "day": day.isoformat(),
            "token": token,
        }
    )
    result = LockResult()
    for _ in range(2):
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            stale, message = _lock_verdict(path, now, pid_alive)
            if not stale:
                raise RunLockedError(message) from None
            result.taken_over = message
            path.unlink(missing_ok=True)
            continue
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
        break
    else:
        raise RunLockedError(f"could not take {path}: someone else took it at the same moment")
    try:
        yield result
    finally:
        mine = _read_lock(path)
        if mine is not None and mine.get("token") == token:
            path.unlink(missing_ok=True)


# -- steps and the run -------------------------------------------------------------


@dataclass
class StepReport:
    """What one step says about itself.

    ``warning`` = worked, but something the owner should see (does not fail the run).
    ``data`` is free-form detail stored in the run record (e.g. the failed downloads).
    """

    status: StepStatus
    detail: str = ""
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DailySteps:
    """The four real jobs of a run. Tests pass fakes; the CLI passes the real ones."""

    collect: Callable[[date, Phase], StepReport]
    build: Callable[[], StepReport]
    export: Callable[[], StepReport]
    alerts: Callable[[], StepReport]


@dataclass(frozen=True)
class RunResult:
    exit_code: int
    record: dict[str, Any]
    record_path: Path


def _stderr(text: str) -> None:
    print(text, file=sys.stderr)


def run_record_path(state_dir: Path, day: date, phase: str) -> Path:
    return Path(state_dir) / RUNS_DIR / f"{day.isoformat()}_{phase}.json"


def run_daily(
    *,
    day: date,
    phase: Phase,
    steps: DailySteps,
    state_dir: Path,
    no_alerts: bool = False,
    clock: Callable[[], datetime] = now_ist,
    out: Callable[[str], None] = print,
    err: Callable[[str], None] = _stderr,
    pid: int | None = None,
    pid_alive: Callable[[int], bool] = _pid_alive,
) -> RunResult:
    """Run collect -> build -> export -> alerts for ``day`` and write the run record.

    Rules:
      * A failed step is reported loudly (``err``) and the run goes on, with two exceptions
        that are plain logic: the export is skipped when the build failed (there is nothing
        new to export), nothing else is skipped. Alerts still run after a failure so the
        "stale data" and "source failed" alerts can fire.
      * The exit code is 1 if any step failed, else 0. A ``warning`` (for example "no
        Telegram credentials") does not fail the run.
      * The record ``<state>/runs/<day>_<phase>.json`` is rewritten after every step, so a
        crash leaves a partial record marked "running" or "interrupted".
    Raises ``RunLockedError`` (nothing written, nothing run) if another run holds the lock.
    """
    state = Path(state_dir)
    started = require_aware(clock(), "clock()")
    with run_lock(state, now=started, phase=phase, day=day, pid=pid, pid_alive=pid_alive) as lock:
        record: dict[str, Any] = {
            "version": RECORD_VERSION,
            "phase": phase,
            "day": day.isoformat(),
            "started_at": started.astimezone(IST).isoformat(timespec="seconds"),
            "finished_at": None,
            "duration_s": None,
            "outcome": "running",
            "exit_code": None,
            "lock_taken_over": lock.taken_over,
            "steps": [],
        }
        path = run_record_path(state, day, phase)
        if lock.taken_over:
            err(f"WARNING: {lock.taken_over}")
        write_atomic(path, record)

        def do(name: str, fn: Callable[[], StepReport]) -> StepReport:
            t0 = clock()
            try:
                report = fn()
            except Exception as exc:  # any step may fail; the run must still report it
                report = StepReport("failed", f"{type(exc).__name__}: {exc}")
            t1 = clock()
            return finish(name, report, t0, t1)

        def finish(name: str, report: StepReport, t0: datetime, t1: datetime) -> StepReport:
            seconds = round((t1 - t0).total_seconds(), 1)
            record["steps"].append(
                {
                    "name": name,
                    "status": report.status,
                    "started_at": t0.astimezone(IST).isoformat(timespec="seconds"),
                    "duration_s": seconds,
                    "detail": report.detail,
                    **({"data": report.data} if report.data else {}),
                }
            )
            write_atomic(path, record)
            label = report.status.upper()
            line = f"[{name}] {label} ({seconds} s) {report.detail}".rstrip()
            (err if report.status in ("failed", "warning") else out)(line)
            return report

        def skip(name: str, why: str) -> StepReport:
            now = clock()
            return finish(name, StepReport("skipped", why), now, now)

        try:
            do("collect", lambda: steps.collect(day, phase))
            built = do("build", steps.build)
            if built.status == "failed":
                skip("export", "the build failed, so there is nothing new to export")
            else:
                do("export", steps.export)
            if no_alerts:
                skip("alerts", "--no-alerts")
            else:
                do("alerts", steps.alerts)
        except BaseException:  # Ctrl-C or a bug in this function: leave an honest record
            record["outcome"] = "interrupted"
            record["finished_at"] = clock().astimezone(IST).isoformat(timespec="seconds")
            write_atomic(path, record)
            raise

        finished = clock()
        failed = [s["name"] for s in record["steps"] if s["status"] == "failed"]
        record["finished_at"] = finished.astimezone(IST).isoformat(timespec="seconds")
        record["duration_s"] = round((finished - started).total_seconds(), 1)
        record["outcome"] = "failed" if failed else "ok"
        record["exit_code"] = 1 if failed else 0
        write_atomic(path, record)
    if failed:
        err(f"run daily {phase} {day.isoformat()}: FAILED steps: {', '.join(failed)}")
    else:
        out(f"run daily {phase} {day.isoformat()}: OK ({record['duration_s']} s)")
    return RunResult(record["exit_code"], record, path)


def read_last_run(state_dir: Path, phase: str) -> dict[str, Any] | None:
    """The run record of ``phase`` with the latest start time, or None."""
    best: tuple[str, dict[str, Any]] | None = None
    folder = Path(state_dir) / RUNS_DIR
    if not folder.is_dir():
        return None
    for file in folder.glob(f"*_{phase}.json"):
        try:
            data = json.loads(file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict) or data.get("phase") != phase:
            continue
        key = str(data.get("started_at") or "")
        if best is None or key > best[0]:
            best = (key, data)
    return best[1] if best else None


def run_status_lines(*, state_dir: Path, now: datetime) -> list[str]:
    """Lines for `etfp status`: the last `run daily` of each phase, step by step."""
    lines: list[str] = []
    for phase in PHASES:
        record = read_last_run(state_dir, phase)
        if record is None:
            lines.append(f"last {phase} run: never")
            continue
        outcome = str(record.get("outcome"))
        started = str(record.get("started_at"))
        if outcome == "running":
            try:
                old = now - datetime.fromisoformat(started) >= STALE_LOCK
            except ValueError:
                old = False
            outcome = "INTERRUPTED (still marked running after 2 h)" if old else "RUNNING"
        elif outcome == "ok":
            warned = sum(1 for s in record.get("steps", []) if s.get("status") == "warning")
            outcome = f"OK ({warned} warning(s))" if warned else "OK"
        else:
            outcome = outcome.upper()
        lines.append(
            f"last {phase} run: {record.get('day')}  started {_age_text(started, now)}  {outcome}"
        )
        for step in record.get("steps", []):
            detail = str(step.get("detail") or "")
            if len(detail) > 140:
                detail = detail[:137] + "..."
            status = str(step.get("status")).upper()
            lines.append(
                f"  {step.get('name'):<8} {status:<8} {step.get('duration_s')} s  {detail}"
            )
    lock = Path(state_dir) / LOCK_NAME
    info = _read_lock(lock)
    if info is not None:
        lines.append(
            f"run lock held: pid {info.get('pid')}, {info.get('phase')} {info.get('day')}, "
            f"since {info.get('started_at')}"
        )
    return lines


def _age_text(then: str | None, now: datetime) -> str:
    if not then:
        return "never"
    try:
        stamp = datetime.fromisoformat(then)
    except ValueError:
        return then
    minutes = int((now - stamp).total_seconds() // 60)
    return f"{then} ({minutes} min ago)"


def status_lines(*, store: RawStore, state_dir: Path | None, now: datetime) -> list[str]:
    """Plain-text report for `etfp status`. Works when nothing has been recorded yet."""
    today: date = now.astimezone(IST).date()
    lines: list[str] = []

    beat = read_heartbeat(state_dir)
    if beat is None:
        lines.append("no ticks recorded yet")
    else:
        lines.append(f"last tick started:  {_age_text(beat.get('last_tick_started'), now)}")
        lines.append(f"last tick finished: {beat.get('last_tick_finished')}")
        lines.append(f"scheduled slot:     {beat.get('scheduled_for') or 'manual'}")
        lines.append(f"sources: {beat.get('ok', 0)} ok, {beat.get('failed', 0)} failed")
        for name, info in (beat.get("sources") or {}).items():
            state = "OK    " if info.get("ok") else "FAILED"
            extra = ""
            if not info.get("ok"):
                extra = f"  {info.get('error_type')}: {info.get('error')}"
            lines.append(f"  {state} {name}  last ok: {info.get('last_ok_at') or 'never'}{extra}")

    lines.append(
        f"failures logged today ({today.isoformat()}): {count_failures_on(today, state_dir)}"
    )

    counts: dict[str, set[str]] = {}
    for entry in store.iter_manifest():
        if entry.get("day") == today.isoformat():
            counts.setdefault(entry["source"], set()).add(entry["relpath"])
    if counts:
        lines.append(f"raw files today ({today.isoformat()}):")
        for source in sorted(counts):
            lines.append(f"  {source}: {len(counts[source])}")
    else:
        lines.append(f"raw files today ({today.isoformat()}): none")

    lines += run_status_lines(
        state_dir=state_dir if state_dir is not None else paths.state_dir(), now=now
    )
    return lines
