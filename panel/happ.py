"""Manage the existing VLESS/Reality inbound, without UUID/port migration."""
import base64
import copy
import hashlib
import io
import ipaddress
import json
import os
import os
from pathlib import Path
import re
import subprocess
import time
from urllib.parse import urlencode, quote
import uuid


class Happ:
    def __init__(self, panel, config=None,
                 binary=None, container=None,
                 access=None, api='127.0.0.1:10085'):
        self.panel, self.store = panel, panel.store
        self.config = Path(config) if config is not None else self.store.root / 'xray-server.json'
        self.binary = Path(binary or os.environ.get('OPENFLUX_XRAY_BINARY', '/usr/local/bin/xray'))
        self.container = container or os.environ.get('OPENFLUX_XRAY_CONTAINER', 'xray-reality-8443')
        self.api = os.environ.get('OPENFLUX_XRAY_API', api)
        self.access = Path(access) if access else None
        self.error = ''
        self.ready = False
        with self.store.db() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS happ_profiles(id TEXT PRIMARY KEY, name TEXT NOT NULL,
              credential TEXT NOT NULL, inbound TEXT NOT NULL, pending TEXT NOT NULL DEFAULT '');
            ''')
        if not panel.demo:
            self.recover_write()
            self.discover()

    def recover_write(self):
        """Replay a complete journal if power failed during a bind-mounted file write."""
        journal = self.store.root / 'xray-write.json'
        if not journal.exists():
            return
        record = json.loads(journal.read_text())
        if record['path'] != str(self.config) or self.config.is_symlink():
            raise ValueError('Unexpected Xray write journal')
        json.loads(record['content'])
        with self.config.open('r+') as file:
            file.write(record['content']); file.truncate(); file.flush(); os.fsync(file.fileno())
        journal.unlink()

    def run(self, args, text=None, timeout=15):
        from server import PanelError
        result = subprocess.run(args, input=text, text=True, capture_output=True, timeout=timeout)
        if result.returncode:
            raise PanelError('Операция Xray не завершена. Проверьте журнал панели; доступ может быть отозван частично.')
        return result.stdout

    def read(self):
        from server import PanelError
        if self.config.is_symlink():
            raise PanelError('Конфигурация Xray не должна быть символической ссылкой.')
        c = json.loads(self.config.read_text())
        candidates = [i for i in c.get('inbounds', []) if i.get('protocol') == 'vless'
                      and i.get('streamSettings', {}).get('security') == 'reality']
        if len(candidates) != 1:
            raise PanelError('Нужна явная настройка поддерживаемого VLESS/Reality inbound.')
        return c, candidates[0]

    @staticmethod
    def identity(client):
        return 'h-' + hashlib.sha256(client['id'].encode()).hexdigest()[:16]

    def discover(self):
        try:
            config, inbound = self.read()
            for index, client in enumerate(inbound['settings'].get('clients', [])):
                ident = self.identity(client)
                with self.store.db() as db:
                    if self.panel.management.deleted(ident):
                        raise ValueError('Deleted credential reappeared in server config')
                    db.execute('INSERT OR IGNORE INTO happ_profiles(id,name,credential,inbound) VALUES (?,?,?,?)',
                               (ident, 'Существующий Happ · ' + str(index + 1), json.dumps(client), inbound.get('tag', '')))
                    # Initial activation adds email/level without changing the UUID.
                    db.execute('UPDATE happ_profiles SET credential=?,inbound=? WHERE id=? AND pending=?',
                               (json.dumps(client), inbound.get('tag', ''), ident, ''))
                self.panel.management.register(ident, 'Существующий Happ · ' + str(index + 1), 'happ', True)
            if self.container and not self.access:
                d = json.loads(self.run(['docker', 'inspect', self.container]))[0]
                volume = next(m['Source'] for m in d['Mounts'] if m['Destination'] == '/var/log/xray')
                self.access = Path(volume) / 'panel-access.log'
            self.ready = bool(inbound.get('tag') and config.get('api') and 'stats' in config
                              and self.binary.exists() and self.access and config.get('log', {}).get('access'))
            self.error = '' if self.ready else 'Управление и персональный учёт требуют первоначального включения API Xray и журнала соединений.'
        except Exception:
            self.ready = False
            self.error = 'Конфигурация Xray недоступна или не поддерживается. Проверьте разрешённый путь конфигурации в службе панели.'

    def all(self):
        with self.store.db() as db:
            return [dict(r) for r in db.execute('SELECT * FROM happ_profiles ORDER BY rowid')]

    def get(self, ident):
        from server import PanelError
        with self.store.db() as db:
            row = db.execute('SELECT * FROM happ_profiles WHERE id=?', (ident,)).fetchone()
        if not row:
            raise PanelError('Пользователь Happ не найден.')
        return dict(row)

    def require_ready(self):
        from server import PanelError
        if not self.ready:
            raise PanelError(self.error)

    def api_call(self, command, *args, text=None):
        if self.container:
            prefix = ['docker', 'exec', '-i', self.container, '/usr/local/bin/xray']
        else:
            prefix = [str(self.binary)]
        return self.run(prefix + ['api', command, '--server=' + self.api, *args], text)

    def runtime_users(self):
        _, inbound = self.read()
        result = json.loads(self.api_call('inbounduser', '-tag=' + inbound['tag']))
        return result.get('users', [])

    def epoch(self):
        if self.container:
            d = json.loads(self.run(['docker', 'inspect', self.container]))[0]
            return d['Id'] + ':' + d['State']['StartedAt']
        return self.run(['systemctl', 'show', 'openflux-xray-test.service', '-p', 'InvocationID', '--value']).strip()

    def sample(self):
        if not self.ready:
            for p in self.all():
                self.panel.management.usage.unavailable(p['id'])
            return
        try:
            values = json.loads(self.api_call('statsquery', '-pattern=user>>>'))
            counters = {s['name']: int(s.get('value', 0)) for s in values.get('stat', [])}
            epoch = self.epoch()
            for p in self.all():
                client = json.loads(p['credential'])
                if not client.get('email'):
                    self.panel.management.usage.unavailable(p['id'])
                    continue
                prefix = 'user>>>' + client['email'] + '>>>traffic>>>'
                self.panel.management.usage.sample(p['id'], 'xray-user', epoch,
                                                  counters.get(prefix + 'downlink', 0), counters.get(prefix + 'uplink', 0))
        except Exception:
            for p in self.all():
                self.panel.management.usage.unavailable(p['id'])

    def state(self):
        return [{'id': p['id'], 'name': p['name'], 'transport': 'vless-reality',
                 'port': self.read()[1]['port'], 'pending': p['pending'],
                 'flow': json.loads(p['credential']).get('flow', ''),
                 'access_enabled': bool(self.panel.management.metadata(p['id']).get('enabled')),
                 'user_id': self.panel.management.metadata(p['id']).get('user_id'),
                 'usage': self.panel.management.usage.get(p['id']), 'manageable': self.ready}
                for p in self.all()]

    def persist(self, config):
        """Validate first. Keep bind-mounted inode so Docker restarts see new data."""
        from server import atomic_write
        temp = self.store.root / ('xray-validate-' + uuid.uuid4().hex + '.json')
        content = json.dumps(config, indent=2) + '\n'
        validation = copy.deepcopy(config)
        # Host validation uses the same binary, but container paths do not exist here.
        validation['log'] = {'access': 'none', 'error': 'none', 'loglevel': 'none'}
        atomic_write(temp, json.dumps(validation))
        try:
            self.run([str(self.binary), 'run', '-test', '-c', str(temp)])
            atomic_write(self.store.root / 'xray-write.json', json.dumps({'path': str(self.config), 'content': content}))
            # File bind mount: replacing host inode would leave Docker on stale content.
            with self.config.open('r+') as file:
                file.write(content)
                file.truncate()
                file.flush()
                os.fsync(file.fileno())
            (self.store.root / 'xray-write.json').unlink()
        finally:
            temp.unlink(missing_ok=True)

    def pending(self, ident, action):
        with self.store.db() as db:
            db.execute('UPDATE happ_profiles SET pending=? WHERE id=?', (action, ident))

    def close_connections(self, email, port):
        """Match exact source endpoint, never block an IP shared by other users."""
        from server import PanelError
        if not self.access or not self.access.exists():
            raise PanelError('Журнал персональных соединений недоступен; закрытие сессий не подтверждено.')
        if self.container:
            d = json.loads(self.run(['docker', 'inspect', self.container]))[0]
            prefix = ['nsenter', '-t', str(d['State']['Pid']), '-n']
        else:
            prefix = []
        for _ in range(3):
            matches = {}
            # Read log without reflecting source IPs/destinations/credentials into API.
            with self.access.open() as stream:
                for line in stream:
                    m = re.search(r'from (?:tcp:)?(\[[0-9a-fA-F:]+\]|[0-9.]+):(\d+).*email: (\S+)', line)
                    if m:
                        matches[(m[1].strip('[]'), int(m[2]))] = m[3]
            for (address, remote_port), owner in matches.items():
                if owner != email:
                    continue
                ipaddress.ip_address(address)
                filt = ['state', 'established', 'sport', '=', ':' + str(port),
                        'dst', address, 'dport', '=', ':' + str(remote_port)]
                self.run(prefix + ['ss', '-K', '-t', '-n', *filt])
                if self.run(prefix + ['ss', '-H', '-t', '-n', *filt]).strip():
                    raise PanelError('Не удалось закрыть соединение этого пользователя; повторите операцию.')
            time.sleep(0.4)

    def control(self, ident, action):
        from server import PanelError
        self.require_ready()
        p = self.get(ident)
        if p['pending'] == 'delete' and action != 'delete':
            raise PanelError('Удаление не завершено. Повторите удаление пользователя.')
        client = json.loads(p['credential'])
        config, inbound = self.read()
        if not client.get('email'):
            raise PanelError('Доступ ещё не принят под персональное управление Xray.')
        self.sample()
        self.pending(ident, action)
        clients = inbound['settings']['clients']
        if action == 'start':
            if not any(c['id'] == client['id'] for c in clients):
                clients.append(client)
            self.persist(config)
            if not any(u.get('email') == client['email'] for u in self.runtime_users()):
                api_inbound = copy.deepcopy(inbound)
                api_inbound['settings']['clients'] = [client]
                self.api_call('adu', '/proc/self/fd/0', text=json.dumps({'inbounds': [api_inbound]}))
            if not any(u.get('email') == client['email'] for u in self.runtime_users()):
                raise PanelError('Xray не подтвердил включение пользователя.')
            self.panel.management.set_enabled(ident, True)
        elif action in ('stop', 'delete'):
            inbound['settings']['clients'] = [c for c in clients if c['id'] != client['id']]
            self.persist(config)
            if any(u.get('email') == client['email'] for u in self.runtime_users()):
                self.api_call('rmu', '-tag=' + inbound['tag'], client['email'])
            if any(u.get('email') == client['email'] for u in self.runtime_users()):
                raise PanelError('Xray ещё принимает этот credential; повторите отзыв.')
            self.close_connections(client['email'], inbound['port'])
            self.panel.management.set_enabled(ident, False)
        else:
            raise PanelError('Перезапуск общей службы из карточки пользователя запрещён.')
        self.pending(ident, '')
        self.panel.management.audit(ident, action, 'ok')
        return {'ok': True}

    def add(self, data):
        from server import profile_name, PanelError
        self.require_ready()
        if len(self.all()) >= 10:
            raise PanelError('Лимит раздела Happ — 10 устройств.')
        name = profile_name(data.get('name'))
        client = {'id': str(uuid.uuid4()), 'flow': 'xtls-rprx-vision', 'level': 0}
        ident = self.identity(client)
        client['email'] = 'panel-' + ident + '@openflux.local'
        _, inbound = self.read()
        with self.store.db() as db:
            db.execute('INSERT INTO happ_profiles(id,name,credential,inbound,pending) VALUES (?,?,?,?,?)',
                       (ident, name, json.dumps(client), inbound['tag'], 'start'))
        self.panel.management.register(ident, name, 'happ', False)
        self.control(ident, 'start')
        self.sample()
        return {'id': ident}

    def edit(self, ident, data):
        from server import PanelError, profile_name
        p = self.get(ident)
        profile_name(data.get('name'))
        meta = self.panel.management.metadata(ident)
        if meta.get('deleting') or p['pending'] == 'delete':
            raise PanelError('Удаление не завершено; изменение профиля запрещено.')
        with self.store.db() as db:
            if not db.execute('SELECT 1 FROM users WHERE id=?', (data.get('user_id',meta['user_id']),)).fetchone():
                raise PanelError('Владелец не найден.')
        client = json.loads(p['credential'])
        flow = data.get('flow', client.get('flow', ''))
        if flow not in ('', 'xtls-rprx-vision'):
            raise PanelError('Выберите Vision или доступ без flow.')
        if flow != client.get('flow', ''):
            self.require_ready()
            enabled = bool(self.panel.management.metadata(ident).get('enabled'))
            if enabled:
                self.control(ident, 'stop')
            client['flow'] = flow
            with self.store.db() as db:
                db.execute('UPDATE happ_profiles SET credential=? WHERE id=?', (json.dumps(client), ident))
            if enabled:
                self.control(ident, 'start')
        return self.panel.management.edit(ident, data)

    def delete(self, ident, data):
        from server import PanelError
        if self.panel.management.deleted(ident):
            return {'ok': True, 'already_deleted': True}
        p = self.get(ident)
        if data.get('confirm_name') != p['name']:
            raise PanelError('Подтвердите удаление именем устройства.')
        with self.store.db() as db:
            db.execute('UPDATE devices SET deleting=1 WHERE id=?', (ident,))
        try:
            self.control(ident, 'delete')
            with self.store.db() as db:
                db.execute('DELETE FROM happ_profiles WHERE id=?', (ident,))
            self.panel.management.finish_delete(ident, 'happ')
            return {'ok': True}
        except Exception:
            self.panel.management.audit(ident, 'delete', 'partial')
            raise

    def share(self, ident):
        from server import PanelError
        p = self.get(ident)
        if p['pending'] == 'delete' or self.panel.management.metadata(ident).get('deleting'):
            raise PanelError('Удаление не завершено; выдача credential запрещена.')
        _, inbound = self.read()
        stream = inbound['streamSettings']
        if stream.get('network') not in ('tcp', 'raw'):
            raise PanelError('Выдача ссылок для этого транспорта не поддерживается.')
        reality = stream['realitySettings']
        # Pinned Xray CLI derives public X25519 key; private value never appears in output/API.
        output = self.run([str(self.binary), 'x25519', '-i', reality['privateKey']])
        m = re.search(r'(?:PublicKey|Password(?: \(PublicKey\))?|Public key):\s*(\S+)', output)
        if not m:
            raise PanelError('Не удалось получить публичный Reality-ключ.')
        client = json.loads(p['credential'])
        params = {'encryption': 'none', 'security': 'reality', 'type': 'tcp',
                  'sni': reality['serverNames'][0], 'fp': 'firefox', 'pbk': m[1],
                  'sid': reality.get('shortIds', [''])[0], 'spx': '/'}
        if client.get('flow'):
            params['flow'] = client['flow']
        host = self.store.direct()['host']
        if not host:
            raise PanelError('Укажите адрес VPS.')
        link = 'vless://' + client['id'] + '@' + host + ':' + str(inbound['port']) + '?' + urlencode(params) + '#' + quote(p['name'])
        import qrcode
        import qrcode.image.svg
        qr = qrcode.make(link, image_factory=qrcode.image.svg.SvgPathImage, border=4)
        buf = io.BytesIO()
        qr.save(buf)
        return {'name': p['name'], 'link': link, 'secret': '', 'direct_port': None,
                'svg': 'data:image/svg+xml;base64,' + base64.b64encode(buf.getvalue()).decode()}

    def logs(self, ident):
        p = self.get(ident)
        client = json.loads(p['credential'])
        if not self.access or not self.access.exists() or not client.get('email'):
            return {'text': 'Персональный журнал недоступен до включения управления Xray.'}
        lines = []
        with self.access.open() as file:
            for line in file:
                if 'email: ' + client['email'] in line:
                    lines.append(line.replace(client['email'], '[пользователь]'))
                    lines = lines[-100:]
        return {'text': ''.join(lines)}
