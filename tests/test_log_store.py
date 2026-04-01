from __future__ import annotations

import logging

from src import log_store


def test_query_logs_filters_by_level_and_since(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "app.db"
    monkeypatch.setattr(log_store, "_DB_PATH", db_path)

    handler = log_store.SQLiteLogHandler(db_path)
    handler.setFormatter(logging.Formatter("%(message)s"))

    old_record = logging.LogRecord(
        name="night_watcher.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="old-entry",
        args=(),
        exc_info=None,
    )
    old_record.created = 100.0
    handler.emit(old_record)

    new_record = logging.LogRecord(
        name="night_watcher.test",
        level=logging.ERROR,
        pathname=__file__,
        lineno=2,
        msg="new-entry",
        args=(),
        exc_info=None,
    )
    new_record.created = 200.0
    handler.emit(new_record)

    assert log_store.query_logs(limit=10) == [
        {
            "timestamp": 200.0,
            "level": "ERROR",
            "logger": "night_watcher.test",
            "message": "new-entry",
        },
        {
            "timestamp": 100.0,
            "level": "INFO",
            "logger": "night_watcher.test",
            "message": "old-entry",
        },
    ]
    assert log_store.query_logs(limit=10, level="error") == [
        {
            "timestamp": 200.0,
            "level": "ERROR",
            "logger": "night_watcher.test",
            "message": "new-entry",
        }
    ]
    assert log_store.query_logs(limit=10, since=150.0) == [
        {
            "timestamp": 200.0,
            "level": "ERROR",
            "logger": "night_watcher.test",
            "message": "new-entry",
        }
    ]
