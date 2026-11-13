"""Memory of downloads that do not exist, so a resumed backfill does not ask again.

Plain words: when we ask NSE for a file from 2022 and the answer is "404 not found", that
file will probably never appear (the exchange simply did not publish that file type then,
or it was a holiday our calendar got wrong). Asking again on every run wastes a polite
request budget and makes a 1,400-day backfill take hours longer each time. So the first
404 is written to ``data/state/backfill_state.json`` and later runs skip it, unless the
user passes ``--retry-unavailable``.

This file is runtime state, not data: it is safe to delete (the next run just asks again).
Raw files themselves are never recorded here; whether a day is "done" is answered by the
raw store.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from etfp.timeutil import now_ist

STATE_FILE_NAME = "backfill_state.json"
_VERSION = 1


@dataclass(frozen=True)
class Unavailable:
    """Why and when we decided a download does not exist."""

    status_code: int | None
    url: str
    checked_at: datetime
    note: str


def key_for(source: str, day: date, name: str) -> str:
    """One entry per (raw source folder, day folder, file name)."""
    return f"{source}|{day.isoformat()}|{name}"


class BackfillState:
    """Tiny JSON file with the downloads known not to exist."""

    def __init__(self, path: Path, entries: dict[str, Unavailable] | None = None) -> None:
        self.path = Path(path)
        self._entries: dict[str, Unavailable] = dict(entries or {})

    # -- reading -----------------------------------------------------------

    @classmethod
    def load(cls, path: Path) -> BackfillState:
        """Read the file; a missing file is an empty state. A damaged file is an error.

        We refuse to guess: silently starting from empty would just re-ask for every 404,
        but silently *dropping* a file the user is mid-way through would hide a problem.
        """
        path = Path(path)
        if not path.exists():
            return cls(path)
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
            if doc.get("version") != _VERSION:
                raise ValueError(f"unknown version {doc.get('version')!r}")
            entries = {
                key: Unavailable(
                    status_code=item["status_code"],
                    url=item["url"],
                    checked_at=datetime.fromisoformat(item["checked_at"]),
                    note=item["note"],
                )
                for key, item in doc["unavailable"].items()
            }
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            raise ValueError(
                f"{path}: backfill state file is damaged ({exc}); fix or delete it "
                "(deleting is safe: the next run just asks the sources again)"
            ) from exc
        return cls(path, entries)

    def is_unavailable(self, source: str, day: date, name: str) -> bool:
        return key_for(source, day, name) in self._entries

    def get(self, source: str, day: date, name: str) -> Unavailable | None:
        return self._entries.get(key_for(source, day, name))

    def __len__(self) -> int:
        return len(self._entries)

    # -- writing -----------------------------------------------------------

    def mark_unavailable(
        self,
        source: str,
        day: date,
        name: str,
        *,
        status_code: int | None,
        url: str,
        note: str = "",
        checked_at: datetime | None = None,
    ) -> None:
        """Remember a 404 and write the file straight away (so Ctrl-C loses nothing)."""
        self._entries[key_for(source, day, name)] = Unavailable(
            status_code=status_code,
            url=url,
            checked_at=checked_at or now_ist(),
            note=note,
        )
        self.save()

    def forget(self, source: str, day: date, name: str) -> None:
        """Drop an entry (the file turned out to exist after all) and save."""
        if self._entries.pop(key_for(source, day, name), None) is not None:
            self.save()

    def save(self) -> None:
        """Write atomically: a crash mid-write leaves the old file, never half a file."""
        doc = {
            "version": _VERSION,
            "unavailable": {
                key: {
                    "status_code": item.status_code,
                    "url": item.url,
                    "checked_at": item.checked_at.isoformat(),
                    "note": item.note,
                }
                for key, item in sorted(self._entries.items())
            },
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=self.path.parent, prefix=".tmp-", suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(doc, fh, indent=1, ensure_ascii=False)
                fh.write("\n")
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_name, self.path)
        finally:
            Path(tmp_name).unlink(missing_ok=True)
