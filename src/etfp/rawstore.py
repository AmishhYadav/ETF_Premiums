"""Immutable store for raw downloads.

Why this exists: the project rule is "raw data is immutable". Whatever a source
sent us on a given day is evidence. If a later cleaning step has a bug we must
be able to go back to the exact bytes. So:

* files live at <root>/<source>/<YYYY-MM-DD>/<name> and are never overwritten,
* if the same file is downloaded again and is identical, nothing is written,
* if it is downloaded again and differs (a source silently revised a file), the
  new bytes go beside the old ones as "<stem>.v2<rest>", then ".v3", ...,
* every save call appends one line to <root>/_manifest.jsonl, so each fetch
  attempt (even a duplicate) can be traced, and every clean number can be tied
  back to a file and its SHA-256 fingerprint.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from etfp.http import FetchResult
from etfp.timeutil import require_aware

MANIFEST_NAME = "_manifest.jsonl"
_MAX_WRITE_ATTEMPTS = 100


@dataclass(frozen=True)
class RawRecord:
    """One save call: where the bytes are and how they got there."""

    source: str
    day: date
    name: str  # file name actually written (may carry a version suffix)
    path: Path  # absolute path
    relpath: str  # relative to the raw root, POSIX style: "<source>/<YYYY-MM-DD>/<name>"
    sha256: str
    size_bytes: int
    url: str
    status_code: int
    fetched_at: datetime  # tz-aware
    version: int  # 1 for the first file with this name, 2 for a changed re-download, ...
    duplicate: bool  # True if identical content was already stored (nothing written)


def _validate_part(label: str, value: str) -> None:
    """Reject anything that could escape the folder or hide a file."""
    if (
        not value
        or "/" in value
        or "\\" in value
        or "\x00" in value
        or ".." in value
        or value.startswith(".")
    ):
        raise ValueError(f"invalid {label} {value!r}: must be a plain, non-hidden file/folder name")


def _split_name(name: str) -> tuple[str, str]:
    """Split at the FIRST dot: 'a.csv.gz' -> ('a', '.csv.gz'); 'plain' -> ('plain', '')."""
    i = name.find(".")
    return (name, "") if i < 0 else (name[:i], name[i:])


def _versioned_name(name: str, version: int) -> str:
    if version == 1:
        return name
    stem, rest = _split_name(name)
    return f"{stem}.v{version}{rest}"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class RawStore:
    """Write-once storage for downloaded files, plus an append-only manifest."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root).expanduser().absolute()  # created on first save

    # -- lookup ----------------------------------------------------------

    def _dir(self, source: str, day: date) -> Path:
        _validate_part("source", source)
        return self.root / source / day.isoformat()

    def path_for(self, source: str, day: date, name: str) -> Path:
        """Where the first (version 1) copy of this file lives."""
        _validate_part("name", name)
        return self._dir(source, day) / name

    def _versions(self, source: str, day: date, name: str) -> dict[int, Path]:
        """All stored versions of a name: {1: path, 2: path, ...}."""
        _validate_part("name", name)
        folder = self._dir(source, day)
        if not folder.is_dir():
            return {}
        stem, rest = _split_name(name)
        pattern = re.compile(rf"^{re.escape(stem)}\.v(\d+){re.escape(rest)}$")
        found: dict[int, Path] = {}
        for entry in folder.iterdir():
            if entry.name == name:
                found[1] = entry
            elif (m := pattern.match(entry.name)) and int(m.group(1)) >= 2:
                found[int(m.group(1))] = entry
        return found

    def exists(self, source: str, day: date, name: str) -> bool:
        """True if any version of this file is stored."""
        return bool(self._versions(source, day, name))

    def latest(self, source: str, day: date, name: str) -> Path | None:
        """Path of the highest stored version, or None."""
        versions = self._versions(source, day, name)
        return versions[max(versions)] if versions else None

    # -- saving ----------------------------------------------------------

    def save(
        self,
        *,
        source: str,
        day: date,
        name: str,
        content: bytes,
        url: str,
        status_code: int,
        fetched_at: datetime,
    ) -> RawRecord:
        """Store bytes exactly as given, never overwriting, and log it in the manifest."""
        _validate_part("source", source)
        _validate_part("name", name)
        require_aware(fetched_at, "fetched_at")
        digest = _sha256(content)

        for _ in range(_MAX_WRITE_ATTEMPTS):
            versions = self._versions(source, day, name)
            if versions:
                top = max(versions)
                existing = versions[top]
                if _sha256(existing.read_bytes()) == digest:
                    return self._finish(
                        source, day, existing, digest, content, url, status_code,
                        fetched_at, top, duplicate=True,
                    )  # fmt: skip
                version = top + 1
            else:
                version = 1
            final = self._dir(source, day) / _versioned_name(name, version)
            try:
                self._write_new(final, content)
            except FileExistsError:
                continue  # somebody else wrote it first: look again, never overwrite
            return self._finish(
                source, day, final, digest, content, url, status_code,
                fetched_at, version, duplicate=False,
            )  # fmt: skip
        raise RuntimeError(f"could not store {source}/{day}/{name}: too many write conflicts")

    def save_result(self, *, source: str, day: date, name: str, result: FetchResult) -> RawRecord:
        """Convenience: store a FetchResult from PoliteClient.get()."""
        return self.save(
            source=source,
            day=day,
            name=name,
            content=result.content,
            url=result.url,
            status_code=result.status_code,
            fetched_at=result.fetched_at,
        )

    @staticmethod
    def _write_new(final: Path, content: bytes) -> None:
        """Write via a temp file, then hard-link into place.

        os.link refuses to replace an existing file (FileExistsError), so even
        two processes racing can never overwrite each other's raw data. The
        temp file is always removed.
        """
        final.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=final.parent, prefix=".tmp-", suffix=".part")
        tmp = Path(tmp_name)
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(content)
                fh.flush()
                os.fsync(fh.fileno())
            tmp.chmod(0o444)  # same inode as `final` after the link: read-only from birth
            os.link(tmp, final)
        finally:
            tmp.unlink(missing_ok=True)

    def _finish(
        self,
        source: str,
        day: date,
        path: Path,
        digest: str,
        content: bytes,
        url: str,
        status_code: int,
        fetched_at: datetime,
        version: int,
        *,
        duplicate: bool,
    ) -> RawRecord:
        record = RawRecord(
            source=source,
            day=day,
            name=path.name,
            path=path,
            relpath=f"{source}/{day.isoformat()}/{path.name}",
            sha256=digest,
            size_bytes=len(content),
            url=url,
            status_code=status_code,
            fetched_at=fetched_at,
            version=version,
            duplicate=duplicate,
        )
        self._append_manifest(record)
        return record

    # -- manifest --------------------------------------------------------

    def manifest_path(self) -> Path:
        return self.root / MANIFEST_NAME

    def _append_manifest(self, record: RawRecord) -> None:
        entry = {
            "source": record.source,
            "day": record.day.isoformat(),
            "name": record.name,
            "relpath": record.relpath,
            "sha256": record.sha256,
            "size_bytes": record.size_bytes,
            "url": record.url,
            "status_code": record.status_code,
            "fetched_at": record.fetched_at.isoformat(),
            "version": record.version,
            "duplicate": record.duplicate,
        }
        line = (json.dumps(entry, ensure_ascii=False) + "\n").encode("utf-8")
        self.root.mkdir(parents=True, exist_ok=True)
        with self.manifest_path().open("ab") as fh:
            fh.write(line)  # one write call per line
            fh.flush()
            os.fsync(fh.fileno())

    def iter_manifest(self) -> Iterator[dict]:
        """Yield manifest entries in the order they were written."""
        path = self.manifest_path()
        if not path.exists():
            return
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    yield json.loads(line)
