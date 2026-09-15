"""Where Sira keeps its runtime state on disk.

Hidden state (the resume memory and the DBOS checkpoint database) lives in a
per-user data directory so that an installed ``sira`` behaves the same from
any working directory. Generated output stays in the working directory
(``--output-dir``) because it is the deliverable, not state.

Default locations (``platformdirs.user_data_dir("sira")``):

- macOS: ``~/Library/Application Support/sira``
- Linux: ``$XDG_DATA_HOME/sira`` (``~/.local/share/sira``)
- Windows: ``%LOCALAPPDATA%\\sira``

Set ``SIRA_DATA_DIR`` to use another directory.
"""

import os
import shutil
from pathlib import Path

import platformdirs

DATA_DIR_ENV = "SIRA_DATA_DIR"
MEMORY_DB_FILENAME = "resume_memory.sqlite3"
DBOS_DB_FILENAME = "dbos.sqlite3"

# Where releases before the data directory wrote the memory database:
# relative to the working directory.
LEGACY_MEMORY_DB_PATH = Path("memory") / MEMORY_DB_FILENAME

# SQLite in WAL mode keeps two sidecar files next to the database.
_SQLITE_SIDECAR_SUFFIXES = ("", "-wal", "-shm")


def data_dir() -> Path:
    """Directory for Sira's runtime state: ``$SIRA_DATA_DIR`` or the platform default."""
    override = os.environ.get(DATA_DIR_ENV)
    if override:
        return Path(override)
    return Path(platformdirs.user_data_dir("sira"))


def memory_db_path() -> Path:
    """Path of the resume memory SQLite database."""
    return data_dir() / MEMORY_DB_FILENAME


def dbos_db_url() -> str:
    """SQLAlchemy URL of the DBOS checkpoint database."""
    return f"sqlite:///{data_dir() / DBOS_DB_FILENAME}"


def migrate_legacy_memory_db() -> Path | None:
    """Move a pre-data-directory memory database into the data directory.

    Returns the new path when a database was moved, ``None`` when there was
    nothing to move. Never overwrites: if the data directory already has a
    database, the legacy file is left where it is.
    """
    if not LEGACY_MEMORY_DB_PATH.is_file():
        return None
    target = memory_db_path()
    if target.exists():
        return None
    target.parent.mkdir(parents=True, exist_ok=True)
    for suffix in _SQLITE_SIDECAR_SUFFIXES:
        source = LEGACY_MEMORY_DB_PATH.with_name(LEGACY_MEMORY_DB_PATH.name + suffix)
        if source.is_file():
            shutil.move(source, target.with_name(target.name + suffix))
    return target
