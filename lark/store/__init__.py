"""Append-only JSONL log with a rebuildable SQLite index.

Layout under the data directory:
    data/YYYY/MM/DD/<kind>.<writer>.jsonl   committed to the private data repo
    .cache/index.sqlite            rebuilt from the logs; never committed

Each JSONL line is {"kind", "key", "ts", "record"}. For keyed kinds the index
keeps the latest snapshot per key; for append-only kinds it keeps every row.
The writer suffix (LARK_WRITER, default "main") keeps concurrent machines, such as
the GPU runner, from editing the same file and producing git conflicts.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import TypeVar

from lark.records import RECORD_BY_KIND, Record
from lark.utils.timez import now_utc

R = TypeVar("R", bound=Record)


class Store:
    def __init__(self, data_dir: str | Path, *, rebuild: bool = False):
        self.data_dir = Path(data_dir)
        self.log_dir = self.data_dir / "data"
        self.cache_dir = self.data_dir / ".cache"
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.index_path = self.cache_dir / "index.sqlite"
        self.writer = os.getenv("LARK_WRITER", "main")
        self._lock = threading.Lock()
        fresh = rebuild or not self.index_path.exists()
        if fresh and self.index_path.exists():
            self.index_path.unlink()
        self.db = sqlite3.connect(self.index_path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self._init_schema()
        if fresh:
            self.rebuild()

    # ---------- schema ----------
    def _init_schema(self) -> None:
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS latest (
              kind TEXT NOT NULL, key TEXT NOT NULL, ts TEXT NOT NULL, seq INTEGER NOT NULL, json TEXT NOT NULL,
              PRIMARY KEY (kind, key)
            );
            CREATE TABLE IF NOT EXISTS events (
              seq INTEGER PRIMARY KEY AUTOINCREMENT,
              kind TEXT NOT NULL, key TEXT, ts TEXT NOT NULL, json TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS events_kind_ts ON events(kind, ts);
            CREATE INDEX IF NOT EXISTS latest_kind_ts ON latest(kind, ts);
            """
        )
        self.db.commit()

    # ---------- writes ----------
    def _log_path(self, ts: str) -> Path:
        y, m, d = ts[:4], ts[5:7], ts[8:10]
        p = self.log_dir / y / m / d
        p.mkdir(parents=True, exist_ok=True)
        return p

    def append(self, record: Record) -> None:
        self.append_many([record])

    def append_many(self, records: list[Record]) -> None:
        if not records:
            return
        ts = now_utc().isoformat(timespec="microseconds")
        with self._lock:
            by_kind: dict[str, list[str]] = {}
            for rec in records:
                kind = rec.KIND
                if not kind:
                    raise ValueError(f"{type(rec).__name__} has no KIND")
                payload = rec.model_dump(mode="json")
                line = json.dumps({"kind": kind, "key": rec.key(), "ts": ts, "record": payload}, ensure_ascii=False)
                by_kind.setdefault(kind, []).append(line)
                self._index(kind, rec.key(), ts, payload)
            folder = self._log_path(ts)
            for kind, lines in by_kind.items():
                with (folder / f"{kind}.{self.writer}.jsonl").open("a", encoding="utf-8") as f:
                    f.write("\n".join(lines) + "\n")
            self.db.commit()

    def _index(self, kind: str, key: str | None, ts: str, payload: dict) -> None:
        js = json.dumps(payload, ensure_ascii=False)
        cur = self.db.execute("INSERT INTO events(kind, key, ts, json) VALUES (?,?,?,?)", (kind, key, ts, js))
        if key is not None:
            self.db.execute(
                "INSERT INTO latest(kind, key, ts, seq, json) VALUES (?,?,?,?,?) "
                "ON CONFLICT(kind, key) DO UPDATE SET ts=excluded.ts, seq=excluded.seq, json=excluded.json",
                (kind, key, ts, cur.lastrowid, js),
            )

    # ---------- rebuild ----------
    def iter_log(self) -> Iterator[dict]:
        for path in sorted(self.log_dir.rglob("*.jsonl")):
            with path.open(encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        yield json.loads(line)

    def rebuild(self) -> int:
        with self._lock:
            self.db.execute("DELETE FROM latest")
            self.db.execute("DELETE FROM events")
            rows = sorted(self.iter_log(), key=lambda r: r["ts"])  # stable: same-ts lines keep file order
            for r in rows:
                self._index(r["kind"], r.get("key"), r["ts"], r["record"])
            self.db.commit()
            return len(rows)

    # ---------- reads ----------
    def get(self, cls: type[R], key: str) -> R | None:
        row = self.db.execute("SELECT json FROM latest WHERE kind=? AND key=?", (cls.KIND, key)).fetchone()
        return cls.model_validate_json(row["json"]) if row else None

    def all(self, cls: type[R], where: str = "", params: tuple = (), order: str = "ts") -> list[R]:
        """Latest snapshots for keyed kinds, every entry for append-only kinds.

        `where` is a SQL fragment over `json` (use json_extract(json, '$.field')).
        """
        table = "latest" if cls.KEY else "events"
        sql = f"SELECT json FROM {table} WHERE kind=?" + (f" AND ({where})" if where else "") + f" ORDER BY {order}"
        return [cls.model_validate_json(r["json"]) for r in self.db.execute(sql, (cls.KIND, *params))]

    def since(self, cls: type[R], ts: str) -> list[R]:
        return self.all(cls, "ts >= ?", (ts,))

    def count(self, cls: type[Record], where: str = "", params: tuple = ()) -> int:
        table = "latest" if cls.KEY else "events"
        sql = f"SELECT COUNT(*) FROM {table} WHERE kind=?" + (f" AND ({where})" if where else "")
        return self.db.execute(sql, (cls.KIND, *params)).fetchone()[0]

    def count_events(self) -> int:
        return self.db.execute("SELECT COUNT(*) FROM events").fetchone()[0]

    def close(self) -> None:
        self.db.close()


def record_class(kind: str) -> type[Record]:
    return RECORD_BY_KIND[kind]
