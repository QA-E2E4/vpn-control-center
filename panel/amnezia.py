"""Manage individual peers in the existing AWG container; never restart it.

Private client keys exist only for peers created here. Existing keys are never
replaced. Container config is authoritative; journaled operations change one
peer, with CAS writes and native runtime removal. No endpoint-based bans.
"""
import base64
import hashlib
import io
import ipaddress
import json
import os
from pathlib import Path
import re
import struct
import time
import uuid
import zlib

CONFIG = '/opt/amnezia/awg/awg0.conf'
TABLE = '/opt/amnezia/awg/clientsTable'
OBFS = ('Jc', 'Jmin', 'Jmax', 'S1', 'S2', 'S3', 'S4', 'H1', 'H2', 'H3', 'H4',
        'I1', 'I2', 'I3', 'I4', 'I5')
LIMITATION = ('Строгая привязка к физическому устройству недоступна: скопированный '
              'ключ неотличим от исходного. Автоблокировка по IP/endpoint не используется.')


def fields(text):
    result = {}
    for line in text.splitlines():
        line = line.split('#', 1)[0].strip()
        if '=' in line:
            key, value = line.split('=', 1)
            key, value = key.strip(), value.strip()
            if key in result:
                raise ValueError('Duplicate config field')
            result[key] = value
    return result


def split_config(text):
    parts = re.split(r'(?m)^\s*\[Peer\]\s*$', text)
    interface = fields(parts[0])
    peers = {}
    for block in parts[1:]:
        peer = fields(block)
        key = peer.get('PublicKey', '')
        validate_key(key)
        if key in peers:
            raise ValueError('Duplicate peer')
        peers[key] = (peer, '[Peer]\n'+block.strip()+'\n')
    return parts[0], interface, peers


def validate_key(key):
    if not isinstance(key, str) or not re.fullmatch(r'[A-Za-z0-9+/]{43}=', key):
        raise ValueError('Invalid AWG key')
    if len(base64.b64decode(key, validate=True)) != 32:
        raise ValueError('Invalid AWG key size')


def identity(key):
    return 'a-'+hashlib.sha256(key.encode()).hexdigest()[:20]


class Amnezia:
    def __init__(self, panel, container=None, interface=None):
        self.panel, self.store = panel, panel.store
        self.container = container or os.environ.get('OPENFLUX_AMNEZIA_CONTAINER', 'amnezia-awg2')
        self.interface = interface or os.environ.get('OPENFLUX_AMNEZIA_INTERFACE', 'awg0')
        self.ready, self.error, self.info = False, '', {}
        with self.store.db() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS amnezia_profiles(
              id TEXT PRIMARY KEY, name TEXT NOT NULL, public_key TEXT UNIQUE NOT NULL,
              peer TEXT NOT NULL, private_key TEXT NOT NULL DEFAULT '',
              pending TEXT NOT NULL DEFAULT '', generation INTEGER NOT NULL DEFAULT 0,
              dns TEXT NOT NULL DEFAULT '1.1.1.1, 1.0.0.1', mtu INTEGER NOT NULL DEFAULT 1280);
            ''')
        if not panel.demo:
            self.sample()

    def command(self, args, text=None):
        from server import run
        return run(['docker', 'exec', '-i', self.container]+args, text)

    def read_file(self, path):
        if path not in (CONFIG, TABLE):
            raise ValueError('Unexpected Amnezia path')
        return self.command(['cat', path])

    def write_file(self, path, previous, content):
        if path not in (CONFIG, TABLE):
            raise ValueError('Unexpected Amnezia path')
        digest = hashlib.sha256(previous.encode()).hexdigest()
        # Constant paths; key material is stdin only. CAS rejects external edits.
        script = '''set -eu
umask 077
exec 9>/opt/amnezia/awg/.openflux-panel.lock
flock -x 9
test "$(sha256sum "$1" | cut -d ' ' -f 1)" = "$2"
t=$(mktemp /opt/amnezia/awg/.panel-write-XXXXXX)
trap 'rm -f "$t"' EXIT
cat > "$t"
sync "$t"
mv "$t" "$1"
sync
'''
        self.command(['sh', '-c', script, 'panel-cas', path, digest], content)

    def runtime(self):
        from server import run
        state = json.loads(run(['docker', 'inspect', self.container]))[0]['State']
        if not state['Running']:
            raise ValueError('AWG container stopped')
        index = self.command(['cat', '/sys/class/net/'+self.interface+'/ifindex']).strip()
        lines = self.command(['awg', 'show', self.interface, 'dump']).splitlines()
        peers = {}
        for line in lines[1:]:
            p = line.split('\t')
            if len(p) != 8:
                raise ValueError('Unsupported AWG dump')
            peers[p[0]] = {'psk': p[1], 'allowed': p[3], 'handshake': int(p[4]),
                           'rx': int(p[5]), 'tx': int(p[6])}
        return state['StartedAt']+':'+index, peers

    def rows(self):
        with self.store.db() as db:
            return [dict(r) for r in db.execute('SELECT * FROM amnezia_profiles ORDER BY id')]

    def get(self, ident):
        from server import PanelError
        with self.store.db() as db:
            row = db.execute('SELECT * FROM amnezia_profiles WHERE id=?', (ident,)).fetchone()
        if not row:
            raise PanelError('Устройство Amnezia не найдено.')
        return dict(row)

    def inspect(self):
        text = self.read_file(CONFIG)
        head, settings, peers = split_config(text)
        if '[Interface]' not in head or settings.get('SaveConfig', '').lower() == 'true':
            raise ValueError('Unsupported persistent config')
        required = ('PrivateKey', 'Address', 'ListenPort', 'S3', 'S4', 'H1', 'H2', 'H3', 'H4')
        if any(k not in settings for k in required):
            raise ValueError('Expected AWG2 settings absent')
        if set(settings) - set(OBFS) - {'PrivateKey','Address','ListenPort','MTU','Table','DNS',
                                       'PreUp','PostUp','PreDown','PostDown','SaveConfig'}:
            raise ValueError('Unknown AWG protocol settings; review compatibility first')
        validate_key(settings['PrivateKey'])
        return text, head, settings, peers

    def sample(self):
        try:
            _, _, settings, configured = self.inspect()
            epoch, runtime = self.runtime()
            names = {r['clientId']: r.get('userData', {}).get('clientName', '')
                     for r in json.loads(self.read_file(TABLE))}
            existing = {r['public_key']: r for r in self.rows()}
            for key, (peer, _) in configured.items():
                ident = identity(key)
                if self.panel.management.deleted(ident):
                    raise ValueError('Deleted peer reappeared externally')
                if key not in existing:
                    if key not in runtime or runtime[key]['allowed'] != peer['AllowedIPs']:
                        raise ValueError('Persistent/runtime peer mismatch')
                    name = names.get(key) or 'Amnezia · '+peer['AllowedIPs']
                    with self.store.db() as db:
                        db.execute('INSERT INTO amnezia_profiles(id,name,public_key,peer) VALUES(?,?,?,?)',
                                   (ident, name, key, json.dumps(peer)))
                    self.panel.management.register(ident, name, 'amnezia', True)
            self.info = {'protocol': 'AmneziaWG 2.x configuration', 'port': int(settings['ListenPort']),
                         'device_binding': False, 'limitation': LIMITATION}
            self.ready, self.error = True, ''
            for row in self.rows():
                meta = self.panel.management.metadata(row['id'])
                if not meta:
                    self.panel.management.register(row['id'], row['name'], 'amnezia', False)
                    meta = self.panel.management.metadata(row['id'])
                if row['pending']:
                    self.panel.management.usage.unavailable(row['id'], 'Операция не завершена; повторите действие')
                    continue
                key = row['public_key']
                enabled = bool(meta['enabled'])
                if enabled != (key in configured) or enabled != (key in runtime):
                    raise ValueError('AWG desired/config/runtime mismatch')
                if enabled:
                    data = runtime[key]
                    peer = json.loads(row['peer'])
                    if configured[key][0] != peer or data['allowed'] != peer['AllowedIPs']:
                        raise ValueError('External peer edit detected')
                    expected_psk = peer.get('PresharedKey') or '(none)'
                    if data['psk'] != expected_psk:
                        raise ValueError('Peer PSK mismatch')
                    # Recent handshake is evidence of recent activity, not physical-device identity.
                    recent = 0 < time.time()-data['handshake'] < 180
                    self.panel.management.usage.sample(row['id'], 'amnezia-peer',
                        epoch+':'+str(row['generation']), data['tx'], data['rx'], recent)
                else:
                    self.panel.management.usage.unavailable(row['id'], 'Доступ отключён; накопленный итог сохранён')
        except Exception:
            self.ready = False
            self.error = 'Amnezia недоступна или конфигурация изменена извне. Проверьте контейнер и повторите обновление.'
            for row in self.rows():
                self.panel.management.usage.unavailable(row['id'])

    def require_ready(self):
        from server import PanelError
        if not self.ready:
            raise PanelError(self.error or 'Amnezia недоступна.')

    def state(self):
        result = []
        for p in self.rows():
            meta = self.panel.management.metadata(p['id'])
            result.append({'id': p['id'], 'name': p['name'], 'kind': 'amnezia',
                'access_enabled': bool(meta.get('enabled')), 'deleting': bool(meta.get('deleting')),
                'user_id': meta.get('user_id'), 'pending': p['pending'], 'manageable': self.ready,
                'address': json.loads(p['peer'])['AllowedIPs'], 'port': self.info.get('port'),
                'exportable': bool(p['private_key']), 'dns': p['dns'], 'mtu': p['mtu'],
                'usage': self.panel.management.usage.get(p['id'])})
        return result

    @staticmethod
    def options(data):
        dns = data.get('dns', '1.1.1.1, 1.0.0.1')
        if not isinstance(dns, str) or not 1 <= len(dns.split(',')) <= 2:
            raise ValueError('Invalid DNS')
        dns = ', '.join(str(ipaddress.IPv4Address(v.strip())) for v in dns.split(','))
        mtu = int(data.get('mtu', 1280))
        if not 1280 <= mtu <= 1420:
            raise ValueError('Invalid MTU')
        return dns, mtu

    def add(self, data):
        from server import profile_name, PanelError
        self.sample(); self.require_ready()
        name = profile_name(data.get('name'))
        dns, mtu = self.options(data)
        _, _, settings, _ = self.inspect()
        network = ipaddress.ip_interface(settings['Address']).network
        if network.version != 4 or network.num_addresses > 65536:
            raise PanelError('Подсеть требует отдельной настройки.')
        used = [ipaddress.ip_network(n.strip()) for p in self.rows()
                for n in json.loads(p['peer'])['AllowedIPs'].split(',')]
        used.append(ipaddress.ip_network(str(ipaddress.ip_interface(settings['Address']).ip)+'/32'))
        address = next((a for a in network.hosts() if not any(a in n for n in used)), None)
        if not address:
            raise PanelError('Нет свободных адресов.')
        private = self.command(['awg', 'genkey']).strip()
        public = self.command(['awg', 'pubkey'], private+'\n').strip()
        psk = self.command(['awg', 'genpsk']).strip()
        for k in (private, public, psk): validate_key(k)
        ident = identity(public)
        peer = {'PublicKey': public, 'PresharedKey': psk, 'AllowedIPs': str(address)+'/32'}
        with self.store.db() as db:
            db.execute('INSERT INTO amnezia_profiles(id,name,public_key,peer,private_key,pending,dns,mtu) VALUES(?,?,?,?,?,?,?,?)',
                       (ident, name, public, json.dumps(peer), private, 'start', dns, mtu))
        self.panel.management.register(ident, name, 'amnezia', False)
        self.control(ident, 'start')
        return {'id': ident}

    def persist_peer(self, row, enabled):
        text, head, _, peers = self.inspect()
        key, peer = row['public_key'], json.loads(row['peer'])
        if key in peers and peers[key][0] != peer:
            raise ValueError('External peer edit; refusing overwrite')
        if enabled and key not in peers:
            # Do not let awg steal AllowedIPs from another peer.
            wanted = [ipaddress.ip_network(v.strip()) for v in peer['AllowedIPs'].split(',')]
            for other, _ in peers.values():
                if any(a.overlaps(ipaddress.ip_network(b.strip())) for a in wanted for b in other['AllowedIPs'].split(',')):
                    raise ValueError('Address overlap')
            peers[key] = (peer, '[Peer]\n'+'\n'.join(k+' = '+v for k,v in peer.items())+'\n')
        if not enabled:
            peers.pop(key, None)
        content = head+''.join(block+'\n' for _, block in peers.values())
        if content != text:
            self.write_file(CONFIG, text, content)

    def sync_table(self, row, delete=False):
        text = self.read_file(TABLE)
        table = json.loads(text)
        key = row['public_key']
        if delete:
            table = [r for r in table if r.get('clientId') != key]
        elif not any(r.get('clientId') == key for r in table):
            table.append({'clientId': key, 'userData': {'clientName': row['name'],
                         'allowed_ips': json.loads(row['peer'])['AllowedIPs']}})
        updated = json.dumps(table, ensure_ascii=False, indent=2)+'\n'
        if json.loads(text) != table:
            self.write_file(TABLE, text, updated)

    def control(self, ident, action):
        from server import PanelError
        if action not in ('start','stop','delete'):
            raise PanelError('Неизвестное действие Amnezia.')
        row = self.get(ident)
        if row['pending'] and row['pending'] != action:
            raise PanelError('Сначала повторите незавершённое действие: '+row['pending'])
        # Even on error, a retry can inspect actual state and finish one peer.
        self.inspect(); self.runtime()
        if not row['pending']:
            self.sample(); self.require_ready()
            self.panel.management.audit(ident, action, 'started')
            with self.store.db() as db:
                db.execute('UPDATE amnezia_profiles SET pending=?,generation=generation+? WHERE id=?',
                           (action, int(action=='start' and not self.panel.management.metadata(ident)['enabled']), ident))
                if action == 'delete': db.execute('UPDATE devices SET deleting=1 WHERE id=?', (ident,))
            row = self.get(ident)
        enabled = action == 'start'
        try:
            self.persist_peer(row, enabled)
            if enabled:
                peer = json.loads(row['peer'])
                args = ['awg','set',self.interface,'peer',row['public_key'],'allowed-ips',peer['AllowedIPs']]
                psk = peer.get('PresharedKey')
                if psk: args += ['preshared-key','/dev/stdin']
                self.command(args, psk+'\n' if psk else None)
            else:
                self.command(['awg','set',self.interface,'peer',row['public_key'],'remove'])
            _, live = self.runtime()
            if (row['public_key'] in live) != enabled:
                raise ValueError('Runtime operation unconfirmed')
            self.sync_table(row, action=='delete')
            if action == 'delete':
                self.panel.management.finish_delete(ident, 'amnezia')
            else:
                with self.store.db() as db:
                    db.execute('UPDATE devices SET enabled=? WHERE id=?', (int(enabled), ident))
                    db.execute("UPDATE amnezia_profiles SET pending='' WHERE id=?", (ident,))
                self.panel.management.audit(ident, action, 'ok')
            self.sample()
            return {'ok': True}
        except Exception:
            self.panel.management.audit(ident, action, 'partial')
            self.sample()
            raise PanelError('Операция Amnezia не завершена. Повторите то же действие; другие peer не изменялись.') from None

    def delete(self, ident, data):
        from server import PanelError
        if self.panel.management.deleted(ident):
            return {'ok': True, 'already_deleted': True}
        if data.get('confirm_name') != self.get(ident)['name']:
            raise PanelError('Подтвердите удаление устройства.')
        return self.control(ident, 'delete')

    def edit(self, ident, data):
        from server import PanelError
        row = self.get(ident)
        if row['pending'] or not row['private_key']:
            raise PanelError('Клиентские параметры этого профиля недоступны для изменения.')
        if set(data)-{'dns','mtu'}:
            raise PanelError('Менять разрешено только DNS и MTU выдаваемого профиля.')
        dns, mtu = self.options(data)
        with self.store.db() as db:
            db.execute('UPDATE amnezia_profiles SET dns=?,mtu=? WHERE id=?', (dns,mtu,ident))
        self.panel.management.audit(ident,'client-options','ok')
        return {'ok':True}

    def share(self, ident):
        from server import PanelError, server_host
        row = self.get(ident)
        if row['pending'] or not row['private_key']:
            raise PanelError('Исходный приватный ключ клиента отсутствует на сервере. Используйте ранее выданный профиль; ключ не заменён.')
        _, _, settings, _ = self.inspect()
        host = server_host(self.store.direct()['host'])
        if not host: raise PanelError('Укажите адрес VPS.')
        public = self.command(['awg','pubkey'], settings['PrivateKey']+'\n').strip()
        validate_key(public)
        peer = json.loads(row['peer'])
        endpoint = ('['+host+']' if ':' in host else host)+':'+settings['ListenPort']
        obfs = {k:settings[k] for k in OBFS if k in settings}
        conf = '[Interface]\nPrivateKey = '+row['private_key']+'\nAddress = '+peer['AllowedIPs']+'\n'
        conf += 'DNS = '+row['dns']+'\nMTU = '+str(row['mtu'])+'\n'
        conf += ''.join(k+' = '+v+'\n' for k,v in obfs.items())
        conf += '\n[Peer]\nPublicKey = '+public+'\n'
        if peer.get('PresharedKey'): conf += 'PresharedKey = '+peer['PresharedKey']+'\n'
        conf += 'AllowedIPs = 0.0.0.0/0\nEndpoint = '+endpoint+'\nPersistentKeepalive = 25\n'
        # Mirrors the official client's AWG import structure, not an admin/SSH profile.
        last = {'config':conf,'hostName':host,'port':int(settings['ListenPort']),
            'client_priv_key':row['private_key'],'client_ip':peer['AllowedIPs'],
            'server_pub_key':public,'psk_key':peer.get('PresharedKey',''),
            'allowed_ips':['0.0.0.0/0'],'mtu':str(row['mtu']),'persistent_keep_alive':'25', **obfs}
        native = {'containers':[{'container':'amnezia-awg2','awg':{
                  'last_config':json.dumps(last),'isThirdPartyConfig':True,
                  'port':settings['ListenPort'],'transport_proto':'udp'}}],
                  'defaultContainer':'amnezia-awg2','description':row['name'],
                  'hostName':host,'dns1':row['dns'].split(',')[0].strip(),
                  'dns2':row['dns'].split(',')[-1].strip()}
        raw = json.dumps(native, ensure_ascii=False).encode()
        link = 'vpn://'+base64.urlsafe_b64encode(struct.pack('!I',len(raw))+zlib.compress(raw,8)).decode().rstrip('=')
        import qrcode, qrcode.image.svg
        qr = qrcode.make(link, image_factory=qrcode.image.svg.SvgPathImage, border=4)
        buf = io.BytesIO(); qr.save(buf)
        return {'name':row['name'],'link':link,'config':conf,'filename':ident+'.conf',
                'svg':'data:image/svg+xml;base64,'+base64.b64encode(buf.getvalue()).decode(),
                'ipv4_only':True}

    def logs(self, ident):
        self.get(ident)
        with self.store.db() as db:
            rows = db.execute('SELECT action,outcome FROM audit WHERE device=? ORDER BY id DESC LIMIT 40', (ident,)).fetchall()
        return {'text':'Журнал действий панели (не журнал содержимого трафика)\n'+
                     '\n'.join(r['action']+': '+r['outcome'] for r in rows)}
