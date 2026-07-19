"""Persist the bounded do-not-repeat history used by joke generation.

The store is deliberately small and defensive. A missing or malformed state
file behaves like an empty history, and write failures never escape into the
assistant response path. Updates use an atomic replacement so a service or
power interruption cannot leave a partially written JSON document in place.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from collections import deque
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable, Iterator, Optional

import fcntl


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_STATE_PATH = BASE_DIR / "state" / "recent_jokes.json"


class RecentJokeHistory:
    """Thread-safe, persistent history of recently generated jokes."""

    def __init__(
        self,
        path: Path = DEFAULT_STATE_PATH,
        *,
        max_entries: int = 50,
    ) -> None:
        self.path = Path(path)
        self.max_entries = max(1, int(max_entries))
        self._lock = threading.RLock()
        self._items = deque(maxlen=self.max_entries)
        self._load_warning_emitted = False
        self._load()

    @staticmethod
    def _clean(value: object) -> str:
        return value.strip() if isinstance(value, str) else ""

    def _replace_items_locked(self, rows: Iterable[object]) -> None:
        unique: list[str] = []
        for row in rows:
            joke = self._clean(row)
            if not joke:
                continue
            if joke in unique:
                unique.remove(joke)
            unique.append(joke)
        self._items = deque(unique[-self.max_entries :], maxlen=self.max_entries)

    @contextmanager
    def _file_lock(self):
        """Serialize state updates made by voice, Telegram, and other processes."""
        handle = None
        locked = False
        lock_path = Path(str(self.path) + ".lock")
        try:
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            handle = lock_path.open("a+")
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            locked = True
        except Exception as exc:
            logging.warning(
                "JOKE_HISTORY_LOCK_FAIL path=%s error=%s",
                lock_path,
                exc,
            )
        try:
            yield
        finally:
            if handle is not None:
                try:
                    if locked:
                        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                    handle.close()
                except Exception:
                    pass

    def _read_rows_locked(self) -> Optional[list[object]]:
        try:
            body = json.loads(self.path.read_text(encoding="utf-8"))
            rows = body.get("jokes", []) if isinstance(body, dict) else body
            if not isinstance(rows, list):
                raise ValueError("jokes must be a list")
            self._load_warning_emitted = False
            return rows
        except FileNotFoundError:
            self._load_warning_emitted = False
            return []
        except Exception as exc:
            if not self._load_warning_emitted:
                logging.warning(
                    "JOKE_HISTORY_LOAD_FAIL path=%s error=%s",
                    self.path,
                    exc,
                )
                self._load_warning_emitted = True
            return None

    def _refresh_locked(self) -> bool:
        rows = self._read_rows_locked()
        if rows is None:
            return False
        self._replace_items_locked(rows)
        return True

    def _load(self) -> None:
        with self._lock, self._file_lock():
            if self._refresh_locked() and self._items:
                logging.info(
                    "JOKE_HISTORY_LOADED path=%s count=%s",
                    self.path,
                    len(self._items),
                )

    def _save_locked(self) -> bool:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = Path(str(self.path) + ".tmp")
            temporary.write_text(
                json.dumps(
                    {"version": 1, "jokes": list(self._items)},
                    indent=2,
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            os.replace(temporary, self.path)
            return True
        except Exception as exc:
            logging.warning(
                "JOKE_HISTORY_SAVE_FAIL path=%s error=%s",
                self.path,
                exc,
            )
            return False

    def snapshot(self) -> list[str]:
        with self._lock, self._file_lock():
            self._refresh_locked()
            return list(self._items)

    def remember(self, joke: str) -> bool:
        joke = self._clean(joke)
        if not joke:
            return False
        with self._lock, self._file_lock():
            self._refresh_locked()
            rows = list(self._items)
            if joke in rows:
                rows.remove(joke)
            rows.append(joke)
            self._replace_items_locked(rows)
            return self._save_locked()

    def clear(self) -> None:
        with self._lock, self._file_lock():
            self._items.clear()
            self._save_locked()

    def __iter__(self) -> Iterator[str]:
        return iter(self.snapshot())

    def __len__(self) -> int:
        return len(self.snapshot())
