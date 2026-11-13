"""Operations helpers: the macOS scheduler job and the `etfp status` report.

Why this exists: the recorder only helps if it actually starts every trading
morning. On a Mac the built-in scheduler is `launchd`. This module only
*writes the text* of the job description (a "plist" file); installing it is a
manual step for the owner (see docs/RUNBOOK.md), so nothing here changes the
machine.
"""

from __future__ import annotations

import plistlib
import shutil
from datetime import date, datetime
from pathlib import Path

from etfp.rawstore import RawStore
from etfp.recorder import count_failures_on, read_heartbeat
from etfp.timeutil import IST

DEFAULT_LABEL = "com.etfp.inav"
CAFFEINATE = "/usr/bin/caffeinate"
BASE_PATH = ("/usr/local/bin", "/opt/homebrew/bin", "/usr/bin", "/bin")

# launchd runs the job in the Mac's LOCAL time zone. 09:10 is five minutes
# before NSE opens at 09:15 India time, so the Mac must be set to India time.
START_HOUR = 9
START_MINUTE = 10
WEEKDAYS = (1, 2, 3, 4, 5)  # launchd: 1 = Monday ... 5 = Friday


def find_uv() -> Path:
    """Locate the `uv` program, or say clearly that it is missing."""
    found = shutil.which("uv")
    if found is None:
        raise FileNotFoundError(
            "could not find 'uv' on PATH; install it (https://docs.astral.sh/uv/) "
            "or pass its full path"
        )
    return Path(found)


def render_inav_plist(*, project_root: Path, uv_path: Path, label: str = DEFAULT_LABEL) -> str:
    """Text of a launchd LaunchAgent that starts the recorder at 09:10, Monday to Friday.

    The recorder is wrapped in ``caffeinate -i``, which stops the Mac from
    going to sleep because it looks idle while the recorder runs. On weekday
    market holidays the job still starts; the recorder checks the exchange
    calendar and exits by itself.
    """
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
            "collect",
            "inav",
            "--loop",
        ],
        "WorkingDirectory": str(root),
        "StartCalendarInterval": [
            {"Weekday": day, "Hour": START_HOUR, "Minute": START_MINUTE} for day in WEEKDAYS
        ],
        "StandardOutPath": str(root / "logs" / "launchd.inav.out.log"),
        "StandardErrorPath": str(root / "logs" / "launchd.inav.err.log"),
        "EnvironmentVariables": {"ETFP_ROOT": str(root), "PATH": ":".join(path_dirs)},
        "RunAtLoad": False,
        "ProcessType": "Background",
    }
    return plistlib.dumps(plist, sort_keys=False).decode("utf-8")


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
    return lines
