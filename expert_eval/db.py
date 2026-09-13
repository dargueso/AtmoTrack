"""db.py — SQLite schema and helpers for the DANA expert evaluation site."""

import datetime as dt
import json
import pathlib
import sqlite3

SCHEMA = (pathlib.Path(__file__).resolve().parent / "schema.sql").read_text()


def now():
    return dt.datetime.now(dt.UTC).isoformat(timespec="seconds")


def connect(path):
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA journal_mode = WAL")
    return con


def init(con):
    con.executescript(SCHEMA)
    con.commit()


def sync_cases(con, eval_dir, case_ids):
    """Upsert case metadata from cases/eval/<case_id>.json."""
    eval_dir = pathlib.Path(eval_dir)
    rows = []
    for cid in case_ids:
        p = eval_dir / f"{cid}.json"
        if not p.exists():
            continue
        e = json.loads(p.read_text())
        rows.append((cid, e["time"], e["category"], json.dumps(e["tags"]), e["algo_n_cols"]))
    con.executemany(
        """INSERT INTO cases (case_id, time, category, tags, algo_n_cols) VALUES (?,?,?,?,?)
           ON CONFLICT(case_id) DO UPDATE SET time=excluded.time, category=excluded.category,
           tags=excluded.tags, algo_n_cols=excluded.algo_n_cols""",
        rows,
    )
    con.commit()
    return len(rows)
