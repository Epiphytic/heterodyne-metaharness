"""Atomic threshold admission, immutable retry batches and retained poll history."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3


from ..task_gates import digest as identity


class State:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.root / 'state.sqlite3')
        self.db.executescript('''
          CREATE TABLE IF NOT EXISTS thresholds(key TEXT PRIMARY KEY, expires REAL);
          CREATE TABLE IF NOT EXISTS outbox(key TEXT PRIMARY KEY, text TEXT, sent REAL);
        ''')

    def enqueue(self, key, text):
        with self.db:
            self.db.execute('INSERT OR IGNORE INTO outbox VALUES (?,?,NULL)', (key, text))

    def admit(self, sample, thresholds):
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            crossed = []
            for threshold in sorted(thresholds):
                key = identity([sample.source, sample.model, sample.interval, sample.start, threshold])
                if sample.percent >= threshold:
                    row = self.db.execute('INSERT OR IGNORE INTO thresholds VALUES (?,?)',
                                          (key, sample.end + 90 * 86400))
                    if row.rowcount:
                        crossed.append(threshold)
            if not crossed:
                return
            key = 'usage:' + identity([sample.source, sample.model, sample.interval, sample.start, crossed])
            text = (f'Quota: {sample.source}/{sample.model} {sample.interval} — '
                    + ', '.join(str(t) + '%' for t in crossed) + '\n'
                    + 'Used | Measurement | Reset (UTC)\n'
                    + f'{sample.percent:.2f}% | {sample.method} | '
                    + datetime.fromtimestamp(sample.end, timezone.utc).isoformat())
            self.db.execute('INSERT INTO outbox VALUES (?,?,NULL)', (key, text))

    async def deliver(self, sender, now):
        for key, text in self.db.execute('SELECT key,text FROM outbox WHERE sent IS NULL ORDER BY rowid').fetchall():
            await sender(text, key)
            with self.db:
                self.db.execute('UPDATE outbox SET sent=? WHERE key=?', (now, key))

    def record(self, record, now):
        date = datetime.fromtimestamp(now, timezone.utc).strftime('%Y-%m-%d')
        with (self.root / (date + '.jsonl')).open('a') as stream:
            stream.write(json.dumps(dict(record, timestamp=now), sort_keys=True) + '\n')
            stream.flush()
            os.fsync(stream.fileno())

    def health(self, errors, now):
        payload = {'state': 'waiting-on-agent' if errors else 'healthy',
                   'errors': errors, 'timestamp': now}
        path = self.root / 'health.json'
        temporary = path.with_suffix('.tmp')
        with temporary.open('w') as stream:
            json.dump(payload, stream)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)

    def retain(self, now):
        cutoff = datetime.fromtimestamp(now - 90 * 86400, timezone.utc).date()
        for path in self.root.glob('????-??-??.jsonl'):
            if datetime.strptime(path.stem, '%Y-%m-%d').date() < cutoff:
                path.unlink()
        with self.db:
            self.db.execute('DELETE FROM thresholds WHERE expires < ?', (now,))
            self.db.execute('DELETE FROM outbox WHERE sent < ?', (now - 90 * 86400,))
