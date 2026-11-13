"""Where things live on disk.

Everything is resolved from one project root so the code never depends on the
current working directory (the scheduler starts jobs from elsewhere).
"""

from __future__ import annotations

import os
from pathlib import Path


def project_root() -> Path:
    """Project root: $ETFP_ROOT if set, else the folder holding pyproject.toml."""
    env = os.environ.get("ETFP_ROOT")
    if env:
        return Path(env).expanduser().resolve()
    # src/etfp/paths.py -> parents[2] is the repo root (editable install).
    return Path(__file__).resolve().parents[2]


def config_dir() -> Path:
    return project_root() / "config"


def raw_dir() -> Path:
    """Untouched downloads: data/raw/<source>/<YYYY-MM-DD>/. Never edited."""
    return project_root() / "data" / "raw"


def clean_dir() -> Path:
    return project_root() / "data" / "clean"


def state_dir() -> Path:
    """Small runtime state (heartbeats, alert de-duplication). Safe to delete."""
    return project_root() / "data" / "state"


def manual_dir() -> Path:
    return project_root() / "data" / "manual"


def logs_dir() -> Path:
    return project_root() / "logs"


def exports_dir() -> Path:
    return project_root() / "exports"
