"""Additive registry migration and scoped, retryable OpenFlux deletion."""
import json
from pathlib import Path
import re
import shutil
import time
import uuid

from telemetry import Usage, IPCMonitor


class Management:
    def __init__(self, panel):
        self.panel = panel
        self.store = panel.store
        self.usage = Usage(self.store)
        self.ipc = IPCMonitor(self.usage)
        self.owner_deletions = set()
        with self.store.db() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY, name TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS devices(id TEXT PRIMARY KEY, user_id TEXT NOT NULL,
              kind TEXT NOT NULL, enabled INTEGER NOT NULL, deleting INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS deleted_profiles(id TEXT PRIMARY KEY, kind TEXT NOT NULL, deleted REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY, device TEXT NOT NULL,
              action TEXT NOT NULL, outcome TEXT NOT NULL, created REAL NOT NULL);
            ''')
        self.sync()

    def sync(self):
        for p in self.store.all():
            s = self.panel.statuses.get(p['unit'], {})
            enabled = s.get('UnitFileState') == 'enabled' or s.get('ActiveState') == 'active'
            self.register(p['id'], p['name'], 'openflux', enabled)

    def register(self, ident, name, kind, enabled):
        with self.store.db() as db:
            if db.execute('SELECT 1 FROM devices WHERE id=?', (ident,)).fetchone():
                return
            db.execute('INSERT OR IGNORE INTO users VALUES (?,?)', ('u-' + ident, name))
            db.execute('INSERT OR IGNORE INTO devices(id,user_id,kind,enabled) VALUES (?,?,?,?)',
                       (ident, 'u-' + ident, kind, int(enabled)))

    def metadata(self, ident):
        with self.store.db() as db:
            row = db.execute('SELECT d.*,u.name AS user_name FROM devices d JOIN users u ON u.id=d.user_id WHERE d.id=?', (ident,)).fetchone()
        return dict(row) if row else {}

    def audit(self, ident, action, outcome):
        with self.store.db() as db:
            db.execute('INSERT INTO audit(device,action,outcome,created) VALUES (?,?,?,?)',
                       (ident, action, outcome, time.time()))

    def set_enabled(self, ident, enabled):
        with self.store.db() as db:
            db.execute('UPDATE devices SET enabled=? WHERE id=?', (int(enabled), ident))

    def edit(self, ident, data):
        from server import profile_name, PanelError
        name = profile_name(data.get('name'))
        owner_name = profile_name(data['owner_name']) if 'owner_name' in data else None
        meta = self.metadata(ident)
        if not meta or meta['deleting']:
            raise PanelError('Профиль недоступен для изменения.')
        user_id = data.get('user_id', meta['user_id'])
        with self.store.db() as db:
            if not db.execute('SELECT 1 FROM users WHERE id=?', (user_id,)).fetchone():
                raise PanelError('Владелец не найден.')
            if owner_name is not None:
                db.execute('UPDATE users SET name=? WHERE id=?', (owner_name, user_id))
            db.execute('UPDATE devices SET user_id=? WHERE id=?', (user_id, ident))
            if meta['kind'] == 'openflux':
                db.execute('UPDATE profiles SET name=? WHERE id=?', (name, ident))
            elif meta['kind'] == 'amnezia':
                db.execute('UPDATE amnezia_profiles SET name=? WHERE id=?', (name, ident))
            else:
                db.execute('UPDATE happ_profiles SET name=? WHERE id=?', (name, ident))
            if user_id != meta['user_id']:
                db.execute('DELETE FROM users WHERE id=? AND NOT EXISTS(SELECT 1 FROM devices WHERE user_id=?)',
                           (meta['user_id'], meta['user_id']))
        self.audit(ident, 'edit', 'ok')
        return {'ok': True}

    def labels(self, ident, data):
        """Presentation metadata only: never call a VPN adapter or service."""
        from server import PanelError
        if set(data) - {'name', 'owner_name', 'user_id'}:
            raise PanelError('Переименование изменяет только визуальные подписи.')
        return self.edit(ident, data)

    def users(self):
        with self.store.db() as db:
            users = [dict(r) for r in db.execute('SELECT * FROM users ORDER BY name')]
            devices = [dict(r) for r in db.execute('SELECT * FROM devices WHERE deleting=0')]
        for user in users:
            ids = [d['id'] for d in devices if d['user_id'] == user['id']]
            values = [self.usage.get(i) for i in ids]
            known = [v for v in values if v['total'] is not None]
            user.update({'devices': ids, 'downloaded': sum(v['downloaded'] for v in known) if known else None,
                         'uploaded': sum(v['uploaded'] for v in known) if known else None,
                         'total': sum(v['total'] for v in known) if known else None,
                         'available': bool(values) and all(v['available'] for v in values),
                         'complete': bool(values) and len(known) == len(values)})
        return users

    def sample(self, profiles, statuses):
        self.sync()
        for p in profiles:
            s = statuses.get(p['unit'], {})
            if s.get('ActiveState') == 'active' and s.get('InvocationID'):
                from server import IPC_ROOT
                path = IPC_ROOT / (p['id'] + '.sock')
                self.ipc.watch(p['id'], path, s['InvocationID'])
            else:
                self.ipc.stop(p['id'])
                self.usage.unavailable(p['id'])

    def finish_delete(self, ident, kind):
        with self.store.db() as db:
            if kind == 'amnezia':
                db.execute('DELETE FROM amnezia_profiles WHERE id=?', (ident,))
            user = db.execute('SELECT user_id FROM devices WHERE id=?', (ident,)).fetchone()
            db.execute('DELETE FROM profiles WHERE id=?', (ident,))
            db.execute('DELETE FROM profile_delivery WHERE id=?', (ident,))
            db.execute('DELETE FROM devices WHERE id=?', (ident,))
            db.execute('DELETE FROM usage WHERE id=?', (ident,))
            db.execute('INSERT OR REPLACE INTO deleted_profiles VALUES (?,?,?)', (ident, kind, time.time()))
            if user and user[0] not in self.owner_deletions:
                db.execute('DELETE FROM users WHERE id=? AND NOT EXISTS(SELECT 1 FROM devices WHERE user_id=?)', (user[0], user[0]))
        self.audit(ident, 'delete', 'ok')

    def deleted(self, ident):
        with self.store.db() as db:
            return bool(db.execute('SELECT 1 FROM deleted_profiles WHERE id=?', (ident,)).fetchone())

    def delete_user(self, ident, data):
        from server import PanelError
        if self.deleted(ident):
            return {'ok': True, 'already_deleted': True}
        with self.store.db() as db:
            owner = db.execute('SELECT * FROM users WHERE id=?', (ident,)).fetchone()
            devices = [dict(r) for r in db.execute('SELECT * FROM devices WHERE user_id=?', (ident,))]
        if not owner or data.get('confirm_name') != owner['name']:
            raise PanelError('Подтвердите удаление именем пользователя.')
        # Check shared backend availability before revoking any device.
        if any(d['kind'] == 'happ' for d in devices):
            self.panel.happ.require_ready()
        if any(d['kind'] == 'amnezia' for d in devices):
            self.panel.amnezia.require_ready()
        self.owner_deletions.add(ident)
        try:
            for device in devices:
                if device['kind'] == 'amnezia':
                    profile = self.panel.amnezia.get(device['id'])
                    self.panel.amnezia.delete(device['id'], {'confirm_name': profile['name']})
                elif device['kind'] == 'happ':
                    profile = self.panel.happ.get(device['id'])
                    self.panel.happ.delete(device['id'], {'confirm_name': profile['name']})
                else:
                    profile = self.store.get(device['id'])
                    self.delete_openflux(device['id'], {'confirm_name': profile['name']})
        finally:
            self.owner_deletions.discard(ident)
        with self.store.db() as db:
            db.execute('DELETE FROM users WHERE id=?', (ident,))
            db.execute('INSERT INTO deleted_profiles VALUES (?,?,?)', (ident, 'user', time.time()))
        self.audit(ident, 'delete-user', 'ok')
        return {'ok': True}

    def delete_openflux(self, ident, data):
        from server import PanelError, CHANNELS, UNITS, IPC_ROOT, MAIN_KEY, run, transport_for_url
        if self.deleted(ident):
            return {'ok': True, 'already_deleted': True}
        p = self.store.get(ident)
        if data.get('confirm_name') != p['name']:
            raise PanelError('Подтвердите удаление именем устройства.')
        key = Path(p['keyfile'])
        if self.panel.demo:
            expected = self.store.root / (ident + '.key')
        elif ident == 'mail-main':
            expected = MAIN_KEY
        elif re.fullmatch(r'm-[a-f0-9]{12}', ident):
            expected = CHANNELS / ident / 'secret.txt'
        else:
            raise PanelError('Неизвестный персональный ресурс; удаление остановлено.')
        expected_unit = 'openflux-mailru.service' if ident == 'mail-main' else 'openflux-panel-' + ident + '.service'
        if key != expected or p['unit'] != expected_unit or key.is_symlink() or key.parent.resolve() != key.parent:
            raise PanelError('Пути профиля не совпадают с разрешёнными ресурсами.')
        with self.store.db() as db:
            db.execute('UPDATE devices SET deleting=1 WHERE id=?', (ident,))
        try:
            self.panel.sample()
            self.ipc.stop(ident)
            if not self.panel.demo:
                self.panel.services.action(p['unit'], 'stop')
                s = self.panel.services.status([p]).get(p['unit'], {})
                if s.get('ActiveState') not in ('inactive', 'failed') or s.get('MainPID') != '0':
                    raise PanelError('Служба ещё работает; повторите удаление.')
                # Delete only this profile's exact files, never /var/lib/openflux.
                for target in (key, key.parent / ('cookies-' + transport_for_url(p['url']) + '.json'),
                               UNITS / p['unit']):
                    if target.is_symlink():
                        raise PanelError('Обнаружена символическая ссылка; удаление остановлено.')
                    target.unlink(missing_ok=True)
                for base in (Path('/run/systemd/system'), UNITS):
                    folder = base / (p['unit'] + '.d')
                    if folder.is_symlink():
                        raise PanelError('Небезопасный путь drop-in.')
                    if folder.exists():
                        shutil.rmtree(folder)
                (IPC_ROOT / (ident + '.sock')).unlink(missing_ok=True)
                if ident != 'mail-main' and key.parent.exists():
                    # Keep unfamiliar files instead of recursively erasing user data.
                    if not any(key.parent.iterdir()):
                        key.parent.rmdir()
                run(['systemctl', 'daemon-reload'])
            else:
                key.unlink(missing_ok=True)
            direct = self.store.direct()
            direct['ports'].pop(ident, None)
            self.store.save_direct(direct)
            self.finish_delete(ident, 'openflux')
            self.panel.sample()
            return {'ok': True}
        except Exception:
            self.audit(ident, 'delete', 'partial')
            raise
