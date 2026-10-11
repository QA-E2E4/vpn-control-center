"""Durable per-device accounting. Old systemd totals are never repurposed."""
import json
from pathlib import Path
import socket
import struct
import threading
import time


class Usage:
    def __init__(self, store):
        self.store = store
        with store.db() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS usage (
              id TEXT PRIMARY KEY, source TEXT NOT NULL, epoch TEXT NOT NULL,
              last_down INTEGER NOT NULL, last_up INTEGER NOT NULL,
              downloaded INTEGER NOT NULL, uploaded INTEGER NOT NULL,
              available INTEGER NOT NULL DEFAULT 0, connected INTEGER,
              note TEXT NOT NULL DEFAULT '', started REAL NOT NULL);
            ''')

    def sample(self, ident, source, epoch, down, up, connected=None):
        if not epoch or any(type(n) is not int or n < 0 or n >= 2**63 for n in (down, up)):
            raise ValueError('Invalid source counters')
        with self.store.lock, self.store.db() as db:
            old = db.execute('SELECT * FROM usage WHERE id=?', (ident,)).fetchone()
            if old is None:
                # No invented traffic before the first observation.
                db.execute('INSERT INTO usage VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                           (ident, source, epoch, down, up, 0, 0, 1, connected, '', time.time()))
            else:
                same = old['source'] == source and old['epoch'] == epoch
                dd = down - old['last_down'] if same and down >= old['last_down'] else down
                du = up - old['last_up'] if same and up >= old['last_up'] else up
                db.execute('UPDATE usage SET source=?,epoch=?,last_down=?,last_up=?,downloaded=downloaded+?,uploaded=uploaded+?,available=1,connected=?,note=? WHERE id=?',
                           (source, epoch, down, up, dd, du, connected, '', ident))

    def unavailable(self, ident, note='Обновление статистики недоступно'):
        with self.store.db() as db:
            db.execute('UPDATE usage SET available=0,connected=NULL,note=? WHERE id=?', (note, ident))

    def get(self, ident):
        with self.store.db() as db:
            row = db.execute('SELECT * FROM usage WHERE id=?', (ident,)).fetchone()
        if row is None:
            return {'downloaded': None, 'uploaded': None, 'total': None, 'available': False,
                    'connected': None, 'note': 'Статистика недоступна'}
        return {k: row[k] for k in ('downloaded', 'uploaded', 'available', 'connected', 'note', 'source')} | {
            'total': row['downloaded'] + row['uploaded']}


def receive(sock, length):
    data = bytearray()
    while len(data) < length:
        part = sock.recv(length - len(data))
        if not part:
            raise EOFError()
        data.extend(part)
    return bytes(data)


class IPCMonitor:
    """One persistent consumer per core socket; never sends commands/cookies."""
    def __init__(self, usage):
        self.usage = usage
        self.workers = {}
        self.lock = threading.Lock()

    def watch(self, ident, path, epoch):
        with self.lock:
            old = self.workers.get(ident)
            if old and old[0] == epoch and old[1].is_alive():
                return
            if old:
                old[2].set()
                old[1].join(timeout=3)
            stop = threading.Event()
            thread = threading.Thread(target=self._loop, args=(ident, path, epoch, stop), daemon=True)
            self.workers[ident] = (epoch, thread, stop)
            thread.start()

    def stop(self, ident):
        with self.lock:
            old = self.workers.pop(ident, None)
        if old:
            old[2].set()
            old[1].join(timeout=3)

    def _loop(self, ident, path, epoch, stop):
        while not stop.is_set():
            try:
                with socket.socket(socket.AF_UNIX) as sock:
                    sock.settimeout(2)
                    sock.connect(str(path))
                    while not stop.is_set():
                        size = struct.unpack('!I', receive(sock, 4))[0]
                        if not 1 <= size <= 1048576:
                            raise ValueError('Invalid IPC frame')
                        frame = receive(sock, size)
                        if frame[0] != 3:
                            continue
                        data = json.loads(frame[1:])
                        if stop.is_set():
                            break
                        self.usage.sample(ident, 'openflux-ipc', epoch,
                                          data['bytes_out'], data['bytes_in'], bool(data.get('connected')))
            except (OSError, EOFError, ValueError, KeyError):
                self.usage.unavailable(ident)
                stop.wait(2)
