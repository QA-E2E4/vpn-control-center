#!/usr/bin/env python3
"""OpenFlux Mail.ru / Yandex panel. Localhost only; reach through an SSH tunnel."""
import argparse
from contextlib import contextmanager
import base64
import io
import ipaddress
import json
import logging
import os
from pathlib import Path
try:
    import pwd
except ImportError:  # Demo/UI work on Windows; production management is Linux-only.
    pwd = None
import re
import secrets
import socket
import sys
import sqlite3
import subprocess
import threading
import time
import uuid
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

VERSION = '0.5.3'
BINARY = os.environ.get('OPENFLUX_CORE_BINARY', '/usr/local/bin/openflux')
MAILRU_BINARY = os.environ.get('OPENFLUX_MAILRU_BINARY', BINARY)
POLLING_DELIVERY = 'mailru-polling'
STATIC = Path(__file__).parent / 'static'
REGISTRY = Path(os.environ.get('OPENFLUX_PANEL_DATA_DIR', '/var/lib/openflux-panel'))
CHANNELS = Path(os.environ.get('OPENFLUX_PANEL_CHANNELS_DIR', '/var/lib/openflux-panel-channels'))
UNITS = Path(os.environ.get('OPENFLUX_SYSTEMD_UNIT_DIR', '/etc/systemd/system'))
IPC_ROOT = Path(os.environ.get('OPENFLUX_IPC_DIR', '/run/openflux-panel-ipc'))
MAIN_KEY = Path(os.environ.get('OPENFLUX_MAIN_KEY', '/var/lib/openflux/secret.txt'))
MAX_PROFILES = 10
DIRECT_PORTS = range(18445, 18545)
PROPS = 'Id,ActiveState,SubState,MainPID,UnitFileState,MemoryCurrent,IPIngressBytes,IPEgressBytes,InvocationID'


class PanelError(Exception):
    pass


def run(args, input_text=None, timeout=20):
    result = subprocess.run(args, input=input_text, capture_output=True, text=True,
                            timeout=timeout, env={**os.environ, 'LC_ALL': 'C', 'SYSTEMD_COLORS': '0'})
    if result.returncode:
        # Do not reflect command output, URLs or key material into errors.
        raise PanelError('Команда не выполнена. Проверьте журнал службы панели.')
    return result.stdout


def document_url(value, transport=None):
    if not isinstance(value, str):
        raise PanelError('Укажите ссылку на документ.')
    if any(ord(c) < 32 for c in value):
        raise PanelError('В ссылке есть недопустимые символы.')
    value = value.strip().rstrip('/')
    if re.fullmatch(r'https://cloud\.mail\.ru/public/[A-Za-z0-9]+/[A-Za-z0-9]+', value):
        detected = 'mailru'
    elif re.fullmatch(r'https://(?:(?:docs|disk)\.yandex\.ru/edit/d/[A-Za-z0-9_-]+(?:\?from_public=1)?|disk\.yandex\.ru/i/[A-Za-z0-9_-]+)', value):
        detected = 'yandex'
    elif re.fullmatch(r'https://telemost\.yandex\.ru/j/[0-9]{1,32}', value):
        detected = 'telemost'
    else:
        raise PanelError('Нужна ссылка документа Mail.ru / Яндекса с редактированием или конференции https://telemost.yandex.ru/j/… .')
    if transport is not None and transport not in ('mailru', 'yandex', 'telemost'):
        raise PanelError('Выберите Mail.ru, Яндекс Документы или Телемост.')
    if transport is not None and transport != detected:
        raise PanelError('Ссылка не соответствует выбранному транспорту.')
    return value


def transport_for_url(url):
    # Revalidate stored URLs before using them in a service or a QR code.
    url = document_url(url)
    if url.startswith('https://telemost.yandex.ru/'):return 'telemost'
    return 'mailru' if url.startswith('https://cloud.mail.ru/') else 'yandex'


def document_identity(url):
    return document_url(url).split('?', 1)[0]


def profile_name(value):
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= 80:
        raise PanelError('Введите имя длиной от 1 до 80 символов.')
    value = value.strip()
    if any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise PanelError('В имени есть недопустимые символы.')
    return value


def atomic_write(path, text, mode=0o600):
    path = Path(path)
    temp = path.with_name(path.name + '.' + secrets.token_hex(4) + '.tmp')
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        with os.fdopen(fd, 'w') as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def server_host(value):
    if not isinstance(value, str):
        raise PanelError('Введите IPv4-адрес или доменное имя VPS без порта и https://.')
    value = value.strip()
    try:
        address = ipaddress.ip_address(value)
        if address.version != 4 or address.is_unspecified or address.is_loopback or address.is_multicast:
            raise ValueError()
        return str(address)
    except ValueError:
        if not re.fullmatch(r'(?=.{1,253}$)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?', value):
            raise PanelError('Введите IPv4-адрес или доменное имя VPS без порта и https://.')
        return value.lower()


def direct_endpoint(host, port):
    host = server_host(host)
    return ('[' + host + ']' if ':' in host else host) + ':' + str(port)


def free_direct_port(used):
    for port in DIRECT_PORTS:
        if port in used:
            continue
        with socket.socket() as probe:
            try:
                probe.bind(('0.0.0.0', port))
            except OSError:
                continue
        return port
    raise PanelError('Нет свободного резервного порта в диапазоне 18445–18544.')


def service_text(directory, url, direct_port=None, client_delivery='native'):
    transport = transport_for_url(url)
    label = {'yandex':'Yandex', 'mailru':'Mail.ru', 'telemost':'Telemost (experimental)'}[transport]
    flags = '--transport=' + transport
    if transport == 'telemost':flags += ' --negotiate'
    binary = MAILRU_BINARY if transport == 'mailru' else BINARY
    environment = 'Environment=OPENFLUX_MAILRU_CURSOR_ONLY=1\n' if transport == 'mailru' else ''
    # Experimental multi-participant WebRTC must not exhaust the small shared VPS.
    resource_limits = 'MemoryHigh=160M\nMemoryMax=256M\nMemorySwapMax=128M\nCPUQuota=70%\n' if transport == 'telemost' else ''
    if client_delivery == POLLING_DELIVERY:
        if transport != 'mailru' or direct_port is not None:
            raise PanelError('Polling поддерживается только для Mail.ru без direct.')
    elif client_delivery != 'native':
        raise PanelError('Неизвестный способ подключения.')
    if direct_port is not None:
        if transport != 'yandex' or type(direct_port) is not int or direct_port not in DIRECT_PORTS:
            raise PanelError('Некорректный резервный порт.')
        flags = f'--transports=yandex:100,direct:50 --direct-listen=0.0.0.0:{direct_port} --negotiate'
    return f'''# Managed by OpenFlux Panel
[Unit]
Description=OpenFlux {label} channel
Wants=network-online.target
After=network-online.target

[Service]
Type=simple
User=openflux
Group=openflux
WorkingDirectory={directory}
{environment}ExecStart={binary} --role=exit --mode=l4 {flags} --url={url} --codec=batched --encryption-key-file={directory}/secret.txt --cookie-store={directory}/cookies-{transport}.json --ipc-socket={IPC_ROOT}/{directory.name}.sock
Restart=on-failure
RestartSec=5
IPAccounting=yes
MemoryAccounting=yes
{resource_limits}UMask=0077
NoNewPrivileges=yes

[Install]
WantedBy=multi-user.target
'''


def unsigned_counter(value):
    try:
        n = int(value)
        return n if 0 <= n < (1 << 63) else None
    except (TypeError, ValueError):
        return None


def memory_info():
    values = {}
    for line in Path('/proc/meminfo').read_text().splitlines():
        k, _, v = line.partition(':')
        values[k] = int(v.strip().split()[0]) * 1024
    return {'total': values['MemTotal'], 'available': values['MemAvailable']}


class Store:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.path = self.root / 'panel.sqlite3'
        self.lock = threading.RLock()
        with self.db() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS profiles (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, url TEXT UNIQUE NOT NULL,
                unit TEXT UNIQUE NOT NULL, keyfile TEXT NOT NULL, created REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS counters (
                id TEXT PRIMARY KEY, invocation TEXT, last_in INTEGER DEFAULT 0,
                last_out INTEGER DEFAULT 0, total_in INTEGER DEFAULT 0,
                total_out INTEGER DEFAULT 0, started REAL, updated REAL,
                verified INTEGER DEFAULT 0);
            CREATE TABLE IF NOT EXISTS profile_delivery (
                id TEXT PRIMARY KEY, client_delivery TEXT NOT NULL
                CHECK(client_delivery='mailru-polling'));
            ''')
        os.chmod(self.path, 0o600)

    @contextmanager
    def db(self):
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def all(self):
        with self.db() as db:
            return [dict(r) for r in db.execute('SELECT * FROM profiles ORDER BY created')]

    def get(self, ident):
        if not re.fullmatch(r'[a-z0-9-]{1,48}', ident):
            raise PanelError('Неверный идентификатор подключения.')
        with self.db() as db:
            r = db.execute('SELECT * FROM profiles WHERE id=?', (ident,)).fetchone()
        if r is None:
            raise PanelError('Подключение не найдено.')
        return dict(r)

    def insert(self, p):
        with self.db() as db:
            db.execute('INSERT INTO profiles VALUES (:id,:name,:url,:unit,:keyfile,:created)', p)
            if p.get('client_delivery') == POLLING_DELIVERY:
                db.execute('INSERT INTO profile_delivery VALUES (?,?)', (p['id'], POLLING_DELIVERY))

    def delivery(self, ident):
        with self.db() as db:
            row = db.execute('SELECT client_delivery FROM profile_delivery WHERE id=?', (ident,)).fetchone()
        return row[0] if row else 'native'

    def direct(self):
        path = self.root / 'direct.json'
        if not path.exists():
            return {'host': '', 'ports': {}}
        try:
            data = json.loads(path.read_text())
            data['host'] = server_host(data['host'])
            ports = data['ports']
            if not isinstance(ports, dict) or any(not re.fullmatch(r'm-[a-f0-9]{12}', k) or type(v) is not int or v not in DIRECT_PORTS for k, v in ports.items()):
                raise ValueError()
            if len(set(ports.values())) != len(ports):
                raise ValueError()
            return {'host': data['host'], 'ports': ports}
        except (ValueError, KeyError, TypeError):
            raise PanelError('Повреждены настройки резервного подключения. Проверьте журнал панели.')

    def save_direct(self, data):
        atomic_write(self.root / 'direct.json', json.dumps(data, indent=2) + '\n')

    def account(self, ident, invocation, incoming, outgoing):
        if not invocation or incoming is None or outgoing is None:
            return
        with self.lock, self.db() as db:
            old = db.execute('SELECT * FROM counters WHERE id=?', (ident,)).fetchone()
            now = time.time()
            if old is None:
                db.execute('INSERT INTO counters VALUES (?,?,?,?,?,?,?,?,?)',
                           (ident, invocation, incoming, outgoing, incoming, outgoing,
                            now, now, int(incoming + outgoing > 0)))
                return
            same = old['invocation'] == invocation
            # systemd starts each new invocation at zero. Never subtract prior totals.
            di = incoming - old['last_in'] if same and incoming >= old['last_in'] else incoming
            do = outgoing - old['last_out'] if same and outgoing >= old['last_out'] else outgoing
            db.execute('UPDATE counters SET invocation=?,last_in=?,last_out=?,total_in=?,total_out=?,updated=?,verified=? WHERE id=?',
                       (invocation, incoming, outgoing, old['total_in'] + di,
                        old['total_out'] + do, now, int(old['verified'] or incoming + outgoing > 0), ident))

    def counter(self, ident):
        with self.db() as db:
            row = db.execute('SELECT * FROM counters WHERE id=?', (ident,)).fetchone()
        return dict(row) if row else None


class Services:
    def status(self, profiles):
        if not profiles:
            return {}
        output = run(['systemctl', 'show', '--no-pager', '--property=' + PROPS] + [p['unit'] for p in profiles])
        result = {}
        for block in output.strip().split('\n\n'):
            fields = dict(line.split('=', 1) for line in block.splitlines() if '=' in line)
            if fields.get('Id'):
                result[fields['Id']] = fields
        return result

    def action(self, unit, action):
        if action == 'start':
            run(['systemctl', 'enable', '--now', unit])
        elif action == 'stop':
            run(['systemctl', 'disable', '--now', unit])
        elif action == 'restart':
            run(['systemctl', 'restart', unit])
        else:
            raise PanelError('Неизвестное действие.')

    def provision(self, p, secret):
        folder = CHANNELS / p['id']
        unitpath = UNITS / p['unit']
        if folder.exists() or unitpath.exists():
            raise PanelError('Каталог или служба уже существуют.')
        user = pwd.getpwnam('openflux')
        folder.mkdir(mode=0o700)
        os.chown(folder, user.pw_uid, user.pw_gid)
        atomic_write(folder / 'secret.txt', secret + '\n')
        os.chown(folder / 'secret.txt', user.pw_uid, user.pw_gid)
        atomic_write(unitpath, service_text(folder, p['url'], p.get('direct_port'), p.get('client_delivery', 'native')), 0o644)
        run(['systemctl', 'daemon-reload'])

    def check_direct(self, unit, port):
        for _ in range(5):
            time.sleep(1)
            pid = run(['systemctl', 'show', unit, '-p', 'MainPID', '--value']).strip()
            listeners = run(['ss', '-lntp', 'sport = :' + str(port)])
            if pid != '0' and 'pid=' + pid + ',' in listeners:
                return
        raise PanelError('Канал создан, но резервный порт не открылся. Посмотрите журнал подключения.')

    def logs(self, unit):
        return run(['journalctl', '-u', unit, '-n', '100', '--no-pager', '-o', 'short-iso'])


class DemoServices(Services):
    def __init__(self, root):
        self.root = Path(root)
        self.states = {}
        self.tick = 0

    def status(self, profiles):
        self.tick += 1
        return {p['unit']: {'Id': p['unit'], 'ActiveState': self.states.get(p['unit'], 'active'),
                'SubState': 'running' if self.states.get(p['unit'], 'active') == 'active' else 'dead',
                'MainPID': '1234' if self.states.get(p['unit'], 'active') == 'active' else '0',
                'UnitFileState': 'enabled' if self.states.get(p['unit'], 'active') == 'active' else 'disabled',
                'MemoryCurrent': str(24 * 1024 * 1024), 'InvocationID': 'demo-' + p['id'],
                'IPIngressBytes': str(self.tick * 135000), 'IPEgressBytes': str(self.tick * 129000)} for p in profiles}

    def action(self, unit, action):
        self.states[unit] = 'inactive' if action == 'stop' else 'active'

    def provision(self, p, secret):
        atomic_write(Path(p['keyfile']), secret + '\n')

    def check_direct(self, unit, port):
        pass

    def logs(self, unit):
        return 'Демонстрационный журнал\n[INIT] Mail.ru: служба запущена\n[M-DOCS] WebSocket connected\n'


class Panel:
    def __init__(self, store, services, demo=False):
        self.store, self.services, self.demo = store, services, demo
        self.lock = threading.RLock()
        self.statuses = {}
        self.last_sample = 0
        self.sample_error = ''
        if demo and not (store.root / 'direct.json').exists():
            store.save_direct({'host': '203.0.113.5', 'ports': {}})
        if not demo:
            self.statuses = services.status(store.all())
        from management import Management
        from happ import Happ
        self.management = Management(self)
        self.happ = Happ(self)
        from amnezia import Amnezia
        self.amnezia = Amnezia(self)

    def sample(self):
        with self.lock:
            try:
                profiles = self.store.all()
                status = self.services.status(profiles)
                for p in profiles:
                    s = status.get(p['unit'], {})
                    if s.get('ActiveState') == 'active':
                        self.store.account(p['id'], s.get('InvocationID'),
                                           unsigned_counter(s.get('IPIngressBytes')),
                                           unsigned_counter(s.get('IPEgressBytes')))
                self.statuses, self.last_sample, self.sample_error = status, time.time(), ''
                if not self.demo:
                    self.management.sample(profiles, status)
                    self.happ.sample()
                    self.amnezia.sample()
                else:
                    self.management.sync()
            except Exception:
                self.sample_error = 'Не удалось обновить состояние служб. Проверьте журнал панели.'
                logging.exception('Status sampling failed')

    def state(self):
        with self.lock:
            direct = self.store.direct()
            profiles = []
            for p in self.store.all():
                s = self.statuses.get(p['unit'], {})
                c = self.store.counter(p['id'])
                profiles.append({k: p[k] for k in ('id', 'name', 'url', 'created')})
                profiles[-1].update({'transport': transport_for_url(p['url']), 'state': s.get('ActiveState', 'unknown'),
                    'client_delivery': 'native',
                    'direct_port': direct['ports'].get(p['id']),
                    'substate': s.get('SubState', ''), 'enabled': s.get('UnitFileState') == 'enabled',
                    'memory': unsigned_counter(s.get('MemoryCurrent')), 'traffic': c})
                meta = self.management.metadata(p['id'])
                profiles[-1].update({'access_enabled': bool(meta.get('enabled')), 'user_id': meta.get('user_id'),
                                     'deleting': bool(meta.get('deleting')), 'usage': self.management.usage.get(p['id'])})
            return {'version': VERSION, 'demo': self.demo, 'profiles': profiles, 'server_host': direct['host'],
                    'memory': {'total': 855*1024*1024, 'available': 225*1024*1024} if self.demo else memory_info(), 'updated': self.last_sample,
                    'error': self.sample_error, 'max_profiles': MAX_PROFILES,
                    'happ': self.happ.state(), 'happ_error': self.happ.error,
                    'happ_ready': self.happ.ready, 'users': self.management.users(),
                    'amnezia': self.amnezia.state(), 'amnezia_ready': self.amnezia.ready,
                    'amnezia_error': self.amnezia.error, 'amnezia_info': self.amnezia.info}

    def add(self, data):
        name = profile_name(data.get('name'))
        url = document_url(data.get('url'), data.get('transport'))
        # Core 0.5.0 and Android >=2.3.3 fix the native WebSocket handshake.
        # Preserve historical delivery rows solely for their existing wire settings.
        delivery = 'native'
        binary = MAILRU_BINARY if transport_for_url(url) == 'mailru' else BINARY
        if not self.demo and not os.access(binary, os.X_OK):
            raise PanelError('Актуальное ядро не установлено. Создание подключения остановлено.')
        # Validate before allocating an id, port, credential or service. Existing
        # profiles and their share links are not rewritten by this preflight.
        if not self.demo and transport_for_url(url) == 'yandex':
            from yandex_document import check_yandex_document, DocumentCheckError
            try:
                check_yandex_document(url)
            except DocumentCheckError as error:
                raise PanelError(str(error)) from None
        with self.lock:
            profiles = self.store.all()
            if len(profiles) >= MAX_PROFILES:
                raise PanelError('В этой версии предусмотрено до 10 подключений. Проверьте ресурсы перед расширением.')
            if any(document_identity(p['url']) == document_identity(url) for p in profiles):
                raise PanelError('Эта ссылка уже используется. Для второго устройства нужен отдельный документ или конференция.')
            ident = 'm-' + uuid.uuid4().hex[:12]
            keyfile = (self.store.root / (ident + '.key')) if self.demo else CHANNELS / ident / 'secret.txt'
            p = {'id': ident, 'name': name, 'url': url, 'unit': 'openflux-panel-' + ident + '.service',
                 'keyfile': str(keyfile), 'created': time.time(), 'client_delivery': delivery}
            if transport_for_url(url) == 'yandex':
                direct = self.store.direct()
                if not direct['host']:
                    raise PanelError('Сначала укажите адрес VPS в разделе «Адрес сервера».')
                p['direct_port'] = free_direct_port(set(direct['ports'].values()))
                direct['ports'][ident] = p['direct_port']
                self.store.save_direct(direct)
            # Register before provisioning so a partially failed channel remains visible.
            self.store.insert(p)
            try:
                self.services.provision(p, secrets.token_hex(32))
                self.services.action(p['unit'], 'start')
                if p.get('direct_port'):
                    self.services.check_direct(p['unit'], p['direct_port'])
            finally:
                self.sample()
            return {'id': ident}

    def control(self, ident, action):
        with self.lock:
            p = self.store.get(ident)
            if action not in ('start', 'stop', 'restart'):
                raise PanelError('Неизвестное действие.')
            if self.management.metadata(ident).get('deleting'):
                raise PanelError('Удаление не завершено. Повторите удаление пользователя.')
            self.sample()  # Save final counters before a deliberate stop/restart.
            self.services.action(p['unit'], action)
            self.management.set_enabled(ident, action != 'stop')
            self.sample()
            return {'ok': True}

    def settings(self, data):
        with self.lock:
            direct = self.store.direct()
            direct['host'] = server_host(data.get('server_host'))
            self.store.save_direct(direct)
            return {'ok': True}

    def share(self, ident):
        p = self.store.get(ident)
        if self.management.metadata(ident).get('deleting'):
            raise PanelError('Удаление не завершено; выдача credential запрещена.')
        secret = Path(p['keyfile']).read_text().strip()
        if not re.fullmatch(r'[0-9a-fA-F]{64}', secret):
            raise PanelError('Файл ключа имеет неожиданный формат.')
        cfg = {'name': p['name'], 'negotiate': False, 'secret': secret,
               'context': p['url'], 'transports': [{'type': transport_for_url(p['url']), 'url': p['url']}]}
        delivery = self.store.delivery(ident)
        if delivery == POLLING_DELIVERY:
            # Keep existing Session/negotiate settings while exporting the fixed
            # native carrier. Do not rewrite user keys, document or context.
            cfg['negotiate'] = True
            cfg['codec'] = 'batched'
        if transport_for_url(p['url']) == 'telemost':
            cfg['negotiate'] = True
            cfg['codec'] = 'batched'
        direct = self.store.direct()
        port = direct['ports'].get(ident)
        if transport_for_url(p['url']) == 'yandex':
            if port is None:
                raise PanelError('Для этого профиля не настроен постоянный резерв. Завершите обновление панели.')
            cfg['negotiate'] = True
            cfg['transports'][0]['priority'] = 100
            cfg['transports'].append({'type': 'direct', 'dial': direct_endpoint(direct['host'], port), 'priority': 50})
        if self.demo:
            co = zlib.compressobj(9, zlib.DEFLATED, -15)
            raw = json.dumps(cfg, ensure_ascii=False).encode()
            link = 'openflux://v1/' + base64.urlsafe_b64encode(co.compress(raw) + co.flush()).decode().rstrip('=')
        else:
            result = json.loads(run([BINARY, '--make-link', '-'], json.dumps(cfg)))
            if result.get('error') or not result.get('link', '').startswith('openflux://v1/'):
                raise PanelError('Ядро не смогло создать ссылку подключения.')
            link = result['link']
            # Round trip through the pinned core, including key and context.
            check = json.loads(run([BINARY, '--parse-link', '-'], link))
            c = check.get('config', {})
            if (check.get('error') or c.get('secret') != secret or c.get('context') != p['url']
                    or c.get('transports') != cfg['transports'] or c.get('negotiate', False) != cfg['negotiate']):
                raise PanelError('Проверка ключа и транспорта в ссылке не пройдена.')
        import qrcode
        import qrcode.image.svg
        qr = qrcode.make(link, image_factory=qrcode.image.svg.SvgPathImage, border=4)
        buf = io.BytesIO()
        qr.save(buf)
        return {'name': p['name'], 'link': link, 'secret': secret, 'direct_port': port,
                'client_delivery': 'native', 'requires_transport_selection': False,
                'script_id': '', 'author_public_key': '',
                'svg': 'data:image/svg+xml;base64,' + base64.b64encode(buf.getvalue()).decode()}

    def logs(self, ident):
        p = self.store.get(ident)
        result = self.services.logs(p['unit'])
        try:
            key = Path(p['keyfile']).read_text().strip()
            if key:
                result = result.replace(key, '[ключ скрыт]')
        except OSError:
            pass
        result = re.sub(r'openflux://\S+', '[ссылка подключения скрыта]', result)
        if transport_for_url(p['url']) == 'telemost':
            # API errors/debug output can contain conference/session credentials.
            result = '\n'.join('[структурированные данные скрыты]' if '{' in line or re.search(r'cookie|credential|token|secret|password', line, re.I) else line for line in result.splitlines())
            result = re.sub(r'(?:https?|wss?)://\S+', '[адрес скрыт]', result)
            result = re.sub(r'\b[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\b|\b[A-Za-z0-9_-]{43,}={0,2}\b', '[идентификатор скрыт]', result, flags=re.I)
        return {'text': result}


class Handler(BaseHTTPRequestHandler):
    server_version = 'OpenFluxPanel'

    def log_message(self, fmt, *args):
        pass  # No access logs containing secrets or profile URLs.

    def reply(self, code, content, mime='application/json; charset=utf-8'):
        if isinstance(content, dict):
            content = json.dumps(content, ensure_ascii=False).encode()
        elif isinstance(content, str):
            content = content.encode()
        self.send_response(code)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(content)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        self.end_headers()
        self.wfile.write(content)

    def authorized(self, api=False):
        host = self.headers.get('Host', '')
        allowed = {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}
        if host not in allowed:
            self.reply(403, {'error': 'Недопустимый адрес панели. Используйте localhost.'})
            return False
        origin = self.headers.get('Origin')
        if origin and origin != 'http://' + host:
            self.reply(403, {'error': 'Запрос из другого источника запрещён.'})
            return False
        if self.headers.get('Sec-Fetch-Site') == 'cross-site':
            self.reply(403, {'error': 'Запрос из другого сайта запрещён.'})
            return False
        if api and not secrets.compare_digest(self.headers.get('X-Panel-Token', ''), self.server.token):
            self.reply(403, {'error': 'Обновите страницу панели.'})
            return False
        return True

    def do_GET(self):
        path = urlsplit(self.path).path
        if not self.authorized(path.startswith('/api/')):
            return
        try:
            if path == '/':
                html = (STATIC / 'index.html').read_text().replace('__PANEL_TOKEN__', self.server.token)
                self.reply(200, html, 'text/html; charset=utf-8')
            elif path in ('/app.js', '/style.css', '/favicon.svg', '/favicon.png'):
                self.reply(200, (STATIC / path[1:]).read_bytes(),
                           {'js': 'text/javascript; charset=utf-8', 'css': 'text/css; charset=utf-8',
                            'svg': 'image/svg+xml', 'png': 'image/png'}[path.rsplit('.',1)[1]])
            elif path == '/api/state':
                self.reply(200, self.server.panel.state())
            elif re.fullmatch(r'/api/profiles/[a-z0-9-]+/logs', path):
                self.reply(200, self.server.panel.logs(path.split('/')[3]))
            elif re.fullmatch(r'/api/happ/[a-z0-9-]+/logs', path):
                self.reply(200, self.server.panel.happ.logs(path.split('/')[3]))
            elif re.fullmatch(r'/api/amnezia/[a-z0-9-]+/logs', path):
                self.reply(200, self.server.panel.amnezia.logs(path.split('/')[3]))
            else:
                self.reply(404, {'error': 'Не найдено.'})
        except PanelError as e:
            self.reply(400, {'error': str(e)})
        except Exception:
            logging.exception('GET failed')
            self.reply(500, {'error': 'Ошибка панели. Посмотрите journalctl -u openflux-panel.'})

    def do_POST(self):
        if not self.authorized(True):
            return
        try:
            if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                self.reply(415, {'error': 'Ожидается JSON.'})
                return
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 4096:
                self.reply(413, {'error': 'Неверный размер запроса.'})
                return
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise PanelError('Неверный запрос.')
            path = urlsplit(self.path).path
            if path == '/api/profiles':
                result = self.server.panel.add(data)
            elif path == '/api/happ':
                with self.server.panel.lock:
                    result = self.server.panel.happ.add(data)
            elif path == '/api/amnezia':
                with self.server.panel.lock:
                    result = self.server.panel.amnezia.add(data)
            elif path == '/api/settings':
                result = self.server.panel.settings(data)
            elif path == '/api/refresh':
                self.server.panel.sample()
                result = self.server.panel.state()
            elif re.fullmatch(r'/api/users/[a-z0-9-]+/delete', path):
                with self.server.panel.lock:
                    result = self.server.panel.management.delete_user(path.split('/')[3], data)
            elif re.fullmatch(r'/api/profiles/[a-z0-9-]+/(start|stop|restart|share|delete|edit|labels)', path):
                _, _, _, ident, action = path.split('/')
                with self.server.panel.lock:
                    if action == 'share':
                        result = self.server.panel.share(ident)
                    elif action == 'delete':
                        result = self.server.panel.management.delete_openflux(ident, data)
                    elif action == 'edit':
                        result = self.server.panel.management.edit(ident, data)
                    elif action == 'labels':
                        result = self.server.panel.management.labels(ident, data)
                    else:
                        result = self.server.panel.control(ident, action)
            elif re.fullmatch(r'/api/amnezia/[a-z0-9-]+/(start|stop|share|delete|edit|labels)', path):
                _, _, _, ident, action = path.split('/')
                with self.server.panel.lock:
                    adapter = self.server.panel.amnezia
                    if action == 'share': result = adapter.share(ident)
                    elif action == 'delete': result = adapter.delete(ident, data)
                    elif action == 'edit': result = adapter.edit(ident, data)
                    elif action == 'labels': result = self.server.panel.management.labels(ident, data)
                    else: result = adapter.control(ident, action)
            elif re.fullmatch(r'/api/happ/[a-z0-9-]+/(start|stop|share|delete|edit|labels)', path):
                _, _, _, ident, action = path.split('/')
                with self.server.panel.lock:
                    if action == 'share':
                        result = self.server.panel.happ.share(ident)
                    elif action == 'delete':
                        result = self.server.panel.happ.delete(ident, data)
                    elif action == 'edit':
                        result = self.server.panel.happ.edit(ident, data)
                    elif action == 'labels':
                        result = self.server.panel.management.labels(ident, data)
                    else:
                        result = self.server.panel.happ.control(ident, action)
            else:
                self.reply(404, {'error': 'Не найдено.'})
                return
            self.reply(200, result)
        except (ValueError, json.JSONDecodeError):
            self.reply(400, {'error': 'Некорректный запрос.'})
        except PanelError as e:
            self.reply(400, {'error': str(e)})
        except Exception:
            logging.exception('POST failed')
            self.reply(500, {'error': 'Операция не завершена. Проверьте список и журнал панели.'})


class WebServer(ThreadingHTTPServer):
    daemon_threads = True

    def get_request(self):
        sock, addr = super().get_request()
        sock.settimeout(20)
        return sock, addr


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--demo', action='store_true')
    ap.add_argument('--data', type=Path, default=REGISTRY)
    ap.add_argument('--port', type=int, default=8787)
    args = ap.parse_args()
    os.umask(0o077)
    store = Store(args.data)
    service = DemoServices(args.data) if args.demo else Services()
    panel = Panel(store, service, args.demo)
    if args.demo and not store.all():
        panel.add({'name': 'Demo · Android', 'url': 'https://cloud.mail.ru/public/Demo/Android'})
        panel.add({'name': 'Demo · iPhone', 'url': 'https://cloud.mail.ru/public/Demo/iPhone'})
    panel.sample()
    web = WebServer(('127.0.0.1', args.port), Handler)
    web.panel, web.token = panel, secrets.token_urlsafe(32)
    def sampler():
        while True:
            time.sleep(10)
            panel.sample()
    threading.Thread(target=sampler, daemon=True).start()
    logging.warning('OpenFlux Panel %s on 127.0.0.1:%s (demo=%s)', VERSION, args.port, args.demo)
    web.serve_forever()


if __name__ == '__main__':
    sys.modules['server'] = sys.modules[__name__]
    main()
