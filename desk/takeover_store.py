from dataclasses import dataclass
import sqlite3


@dataclass(frozen=True)
class TakeoverRecord:
    request_id: str
    batch_id: str
    from_press: str
    to_press: str
    to_run_id: str | None


def init_takeover_store(db: sqlite3.Connection):
    db.execute('''CREATE TABLE IF NOT EXISTS takeover_requests (
        request_id TEXT PRIMARY KEY,
        batch_id TEXT NOT NULL,
        from_press TEXT NOT NULL,
        to_press TEXT NOT NULL,
        to_run_id TEXT
    )''')
    db.execute('''CREATE UNIQUE INDEX IF NOT EXISTS takeover_one_per_batch
                  ON takeover_requests(batch_id)''')
    db.execute('''CREATE TABLE IF NOT EXISTS takeover_qualifications (
        batch_id TEXT NOT NULL,
        request_id TEXT NOT NULL,
        number INTEGER NOT NULL,
        PRIMARY KEY(batch_id, number)
    )''')


def _record(row) -> TakeoverRecord:
    request_id, batch_id, from_press, to_press, to_run_id = row
    return TakeoverRecord(request_id, batch_id, from_press, to_press, to_run_id)


def get_takeover_by_request(db: sqlite3.Connection, request_id: str) -> TakeoverRecord | None:
    row = db.execute('''SELECT request_id, batch_id, from_press, to_press, to_run_id
                        FROM takeover_requests WHERE request_id = ?''',
                     (request_id,)).fetchone()
    return None if row is None else _record(row)


def get_takeover_for_batch(db: sqlite3.Connection, batch_id: str) -> TakeoverRecord | None:
    row = db.execute('''SELECT request_id, batch_id, from_press, to_press, to_run_id
                        FROM takeover_requests WHERE batch_id = ?''',
                     (batch_id,)).fetchone()
    return None if row is None else _record(row)


def insert_takeover_request(db: sqlite3.Connection, record: TakeoverRecord):
    db.execute('''INSERT INTO takeover_requests
                  (request_id, batch_id, from_press, to_press, to_run_id)
                  VALUES (?, ?, ?, ?, ?)''',
               (record.request_id, record.batch_id, record.from_press,
                record.to_press, record.to_run_id))


def set_takeover_run(db: sqlite3.Connection, request_id: str, to_run_id: str):
    db.execute('UPDATE takeover_requests SET to_run_id = ? WHERE request_id = ?',
               (to_run_id, request_id))


def add_qualifications(db: sqlite3.Connection, batch_id: str,
                       request_id: str, numbers):
    db.executemany('''INSERT OR IGNORE INTO takeover_qualifications
                      (batch_id, request_id, number) VALUES (?, ?, ?)''',
                   ((batch_id, request_id, number) for number in numbers))


def get_qualifications(db: sqlite3.Connection, batch_id: str) -> tuple[int, ...]:
    rows = db.execute('''SELECT number FROM takeover_qualifications
                         WHERE batch_id = ? ORDER BY number''', (batch_id,)).fetchall()
    return tuple(row[0] for row in rows)
