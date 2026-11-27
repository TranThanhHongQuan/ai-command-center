"""Bộ lập lịch quét RSS chạy trong tiến trình backend + khóa (lease) trong SQLite
để không chạy trùng giữa thủ công/tự động hoặc giữa nhiều worker cùng một DB."""
from __future__ import annotations
import datetime as dt
import json
import logging
import threading
import uuid

import rss

log = logging.getLogger('aicc.scheduler')
LEASE_TTL = 900  # giây; được gia hạn sau mỗi nguồn

SCHEMA = '''
CREATE TABLE IF NOT EXISTS collect_runs (id TEXT PRIMARY KEY, trigger TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT, status TEXT NOT NULL, sources_total INTEGER NOT NULL DEFAULT 0, added INTEGER NOT NULL DEFAULT 0, skipped INTEGER NOT NULL DEFAULT 0, error_count INTEGER NOT NULL DEFAULT 0, errors TEXT);
CREATE TABLE IF NOT EXISTS locks (name TEXT PRIMARY KEY, owner TEXT NOT NULL, expires_at TEXT NOT NULL);
'''


def _iso(t: dt.datetime) -> str:
    return t.astimezone(dt.timezone.utc).isoformat(timespec='seconds')


def _after(seconds: int) -> str:
    return _iso(dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=seconds))


def acquire_lease(db, name: str, owner: str, ttl: int = LEASE_TTL) -> bool:
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        row = c.execute('SELECT owner,expires_at FROM locks WHERE name=?', (name,)).fetchone()
        if row and row['expires_at'] > rss.now() and row['owner'] != owner:
            return False
        c.execute('INSERT OR REPLACE INTO locks(name,owner,expires_at) VALUES(?,?,?)', (name, owner, _after(ttl)))
        return True


def renew_lease(db, name: str, owner: str, ttl: int = LEASE_TTL) -> None:
    with db() as c:
        c.execute('UPDATE locks SET expires_at=? WHERE name=? AND owner=?', (_after(ttl), name, owner))


def release_lease(db, name: str, owner: str) -> None:
    with db() as c:
        c.execute('DELETE FROM locks WHERE name=? AND owner=?', (name, owner))


def run_collection(db, trigger: str, fetcher=None) -> dict | None:
    """Quét mọi nguồn đang bật và ghi kết quả vào collect_runs. Trả None nếu đang có lượt quét khác."""
    owner = str(uuid.uuid4())
    if not acquire_lease(db, 'collect', owner):
        return None
    run_id, started = str(uuid.uuid4()), rss.now()
    try:
        with db() as c:
            c.execute("UPDATE collect_runs SET status='error',finished_at=?,errors=? WHERE status='running' AND started_at<?",
                      (started, json.dumps(['Bị gián đoạn']), _after(-LEASE_TTL)))
            srcs = [dict(r) for r in c.execute('SELECT * FROM sources WHERE enabled=1 AND source_type='rss'')]
            c.execute('INSERT INTO collect_runs(id,trigger,started_at,status,sources_total) VALUES(?,?,?,?,?)',
                      (run_id, trigger, started, 'running', len(srcs)))
        results = []
        try:
            for s in srcs:
                results.append(rss.collect_source(db, s, fetcher))
                renew_lease(db, 'collect', owner)
            errors = [f"{r['source']}: {r['error']}" for r in results if not r['ok']]
            status = 'ok' if not errors else ('error' if len(errors) == len(results) else 'partial')
        except Exception as ex:  # noqa: BLE001
            errors, status = [f'{type(ex).__name__}: {str(ex)[:160]}'], 'error'
        out = {'run_id': run_id, 'status': status, 'added': sum(r['added'] for r in results),
               'skipped': sum(r['skipped'] for r in results), 'errors': errors, 'results': results}
        with db() as c:
            c.execute('UPDATE collect_runs SET finished_at=?,status=?,added=?,skipped=?,error_count=?,errors=? WHERE id=?',
                      (rss.now(), status, out['added'], out['skipped'], len(errors), json.dumps(errors, ensure_ascii=False), run_id))
        return out
    finally:
        release_lease(db, 'collect', owner)


def recent_runs(db, limit: int = 10) -> list[dict]:
    with db() as c:
        rows = [dict(r) for r in c.execute('SELECT * FROM collect_runs ORDER BY started_at DESC LIMIT ?', (limit,))]
    for r in rows:
        r['errors'] = json.loads(r['errors'] or '[]')
    return rows


class Scheduler:
    """Kiểm tra định kỳ; chạy khi chưa có lượt quét nào trong `interval_min` phút (đọc từ DB,
    nên khởi động lại hoặc nhiều worker không gây quét trùng)."""

    def __init__(self, db, interval_min: int = 60, tick_seconds: float = 30, fetcher=None):
        self.db, self.interval_min, self.tick_seconds, self.fetcher = db, interval_min, tick_seconds, fetcher
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def last_started(self) -> str | None:
        with self.db() as c:
            r = c.execute("SELECT MAX(started_at) FROM collect_runs WHERE status!='running'").fetchone()
        return r[0] if r else None

    def next_run_at(self) -> str | None:
        last = self.last_started()
        if not last:
            return None
        return _iso(dt.datetime.fromisoformat(last) + dt.timedelta(minutes=self.interval_min))

    def is_due(self) -> bool:
        nxt = self.next_run_at()
        return nxt is None or nxt <= rss.now()

    def tick(self) -> dict | None:
        return run_collection(self.db, 'scheduled', self.fetcher) if self.is_due() else None

    def _loop(self):
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:  # noqa: BLE001 - scheduler không được chết vì một lượt lỗi
                log.exception('Lượt quét tự động lỗi')
            self._stop.wait(self.tick_seconds)

    def start(self):
        if not self.running:
            self._stop.clear()
            self._thread = threading.Thread(target=self._loop, name='rss-scheduler', daemon=True)
            self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
