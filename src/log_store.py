"""SQLite-backed log handler for persistent structured log storage.

Provides a :class:`SQLiteLogHandler` that can be attached to any Python
logger, and a :func:`query_logs` helper for the API to surface recent
entries to the Streamlit dashboard.
"""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Any

_DB_PATH = Path("/assets/logs/app.db")


def _ensure_db(path: Path) -> None:
    """Create the database file and schema if they do not exist."""
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path), check_same_thread=False)
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS logs (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp REAL    NOT NULL,
            level     TEXT    NOT NULL,
            logger    TEXT    NOT NULL,
            message   TEXT    NOT NULL
        )
        """
    )
    con.execute("CREATE INDEX IF NOT EXISTS idx_ts ON logs (timestamp)")
    con.commit()
    con.close()


class SQLiteLogHandler(logging.Handler):
    """Logging handler that persists records to a local SQLite database.

    Parameters
    ----------
    db_path:
        Path to the SQLite file. Defaults to ``/assets/logs/app.db``.
    """

    def __init__(self, db_path: Path = _DB_PATH) -> None:
        super().__init__()
        self._db_path = db_path
        _ensure_db(db_path)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            con = sqlite3.connect(str(self._db_path), check_same_thread=False)
            con.execute(
                "INSERT INTO logs (timestamp, level, logger, message) VALUES (?,?,?,?)",
                (record.created, record.levelname, record.name, self.format(record)),
            )
            con.commit()
            con.close()
        except Exception:
            self.handleError(record)


def query_logs(
    limit: int = 200,
    level: str | None = None,
    since: float | None = None,
) -> list[dict[str, Any]]:
    """Return recent log entries ordered newest first.

    Parameters
    ----------
    limit:
        Maximum number of records to return.
    level:
        If set (and not ``"ALL"``), filter to this exact level name
        (e.g. ``"ERROR"``).
    since:
        UNIX timestamp; only return records newer than this value.

    Returns
    -------
    list[dict[str, Any]]
        Each entry has keys: ``timestamp``, ``level``, ``logger``,
        ``message``.  Returns an empty list on any database error.
    """
    try:
        _ensure_db(_DB_PATH)
        conditions: list[str] = []
        params: list[Any] = []
        if level and level.upper() != "ALL":
            conditions.append("level = ?")
            params.append(level.upper())
        if since is not None:
            conditions.append("timestamp > ?")
            params.append(since)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        params.append(limit)
        con = sqlite3.connect(str(_DB_PATH), check_same_thread=False)
        rows = con.execute(
            f"SELECT timestamp, level, logger, message FROM logs"
            f" {where} ORDER BY timestamp DESC LIMIT ?",
            params,
        ).fetchall()
        con.close()
        return [
            {"timestamp": r[0], "level": r[1], "logger": r[2], "message": r[3]}
            for r in rows
        ]
    except Exception:
        return []
