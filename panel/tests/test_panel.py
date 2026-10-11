import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from server import Store, Panel, DemoServices, Handler, WebServer, PanelError, service_text, MAILRU_BINARY
from telemetry import Usage


class PanelTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name))
        self.panel = Panel(self.store, DemoServices(self.temp.name), True)

    def tearDown(self):
        self.temp.cleanup()

    def add(self, name='test', suffix='One'):
        return self.panel.add({'name': name, 'url': 'https://cloud.mail.ru/public/Test/' + suffix})['id']

    def test_mailru_default_bootstrap_and_persistence(self):
        ident = self.add()
        self.assertEqual(self.store.delivery(ident), 'native')
        self.assertEqual(Store(self.store.root).delivery(ident), 'native')
        share = self.panel.share(ident)
        self.assertFalse(share['requires_transport_selection'])
        self.assertEqual(share['script_id'], '')
        import base64, zlib
        encoded = share['link'].split('/')[-1]
        cfg = json.loads(zlib.decompress(base64.urlsafe_b64decode(encoded + '=' * (-len(encoded) % 4)), -15))
        self.assertFalse(cfg['negotiate'])
        self.assertEqual(cfg.get('codec', 'batched'), 'batched')
        self.assertEqual(cfg['context'], self.store.get(ident)['url'])
        self.assertEqual(cfg['secret'], Path(self.store.get(ident)['keyfile']).read_text().strip())
        self.assertEqual([t['type'] for t in cfg['transports']], ['mailru'])
        self.panel.management.delete_openflux(ident, {'confirm_name': 'test'})
        self.assertEqual(self.store.delivery(ident), 'native')

    def test_existing_profiles_are_not_migrated(self):
        ident = self.add()
        with self.store.db() as db:
            db.execute('DELETE FROM profile_delivery WHERE id=?', (ident,))
        key = Path(self.store.get(ident)['keyfile']).read_bytes()
        new = Panel(Store(self.store.root), DemoServices(self.temp.name), True)
        self.assertEqual(new.store.delivery(ident), 'native')
        self.assertFalse(new.share(ident)['requires_transport_selection'])
        self.assertEqual(Path(new.store.get(ident)['keyfile']).read_bytes(), key)

    def test_polling_unit_and_yandex_isolation(self):
        folder = Path('/var/lib/openflux-panel-channels/m-synthetic')
        url = 'https://cloud.mail.ru/public/Test/Synthetic'
        text = service_text(folder, url, client_delivery='mailru-polling')
        self.assertIn('ExecStart=' + MAILRU_BINARY, text)
        self.assertIn('Environment=OPENFLUX_MAILRU_CURSOR_ONLY=1', text)
        self.assertIn('--transport=mailru', text)
        self.assertNotIn('--direct-', text)
        self.assertNotIn('--debug', text)
        native = service_text(folder, url)
        self.assertIn('CURSOR_ONLY=1', native)
        yandex = self.panel.add({'name': 'Y', 'url': 'https://disk.yandex.ru/i/Synthetic'})['id']
        self.assertEqual(self.store.delivery(yandex), 'native')
        self.assertTrue(self.panel.share(yandex)['direct_port'])
        with self.assertRaises(PanelError):
            service_text(folder, 'https://disk.yandex.ru/i/Synthetic', client_delivery='mailru-polling')

    def test_polling_missing_binary_fails_before_registration(self):
        self.panel.demo = False
        with patch('server.os.access', return_value=False):
            with self.assertRaises(PanelError):
                self.add()
        self.assertEqual(self.store.all(), [])

    def test_historical_polling_preserves_session_but_exports_native(self):
        ident = self.add()
        with self.store.db() as db:
            db.execute('INSERT INTO profile_delivery VALUES (?,?)', (ident, 'mailru-polling'))
        before = Path(self.store.get(ident)['keyfile']).read_bytes()
        share = self.panel.share(ident)
        self.assertEqual(share['client_delivery'], 'native')
        self.assertFalse(share['requires_transport_selection'])
        import base64, zlib
        encoded=share['link'].split('/')[-1]
        cfg=json.loads(zlib.decompress(base64.urlsafe_b64decode(encoded+'='*(-len(encoded)%4)),-15))
        self.assertTrue(cfg['negotiate'])
        self.assertEqual(cfg['codec'], 'batched')
        self.assertEqual(cfg['secret'], before.decode().strip())
        self.assertEqual(self.store.delivery(ident), 'mailru-polling')

    def test_usage_restart_reset_unavailable(self):
        u = self.panel.management.usage
        u.sample('a', 'xray', 'epoch1', 1000, 2000)
        u.sample('a', 'xray', 'epoch1', 1500, 2300)
        u.sample('a', 'xray', 'epoch1', 1500, 2300)
        self.assertEqual(u.get('a')['total'], 800)
        # New panel instance uses the exact persisted high-water marks.
        u = Usage(Store(self.store.root))
        u.sample('a', 'xray', 'epoch1', 1600, 2400)
        self.assertEqual(u.get('a')['total'], 1000)
        u.sample('a', 'xray', 'epoch2', 50, 70)
        self.assertEqual(u.get('a')['total'], 1120)
        u.sample('a', 'xray', 'epoch2', 10, 20)
        self.assertEqual(u.get('a')['total'], 1150)
        u.unavailable('a')
        self.assertEqual(u.get('a')['total'], 1150)
        self.assertFalse(u.get('a')['available'])
        self.assertIsNone(u.get('never')['total'])

    def test_legacy_counters_preserved(self):
        ident = self.add()
        self.store.account(ident, 'old', 9999, 3333)
        before = self.store.counter(ident)
        panel = Panel(Store(self.store.root), DemoServices(self.temp.name), True)
        self.assertEqual(before, panel.store.counter(ident))
        self.assertIsNone(panel.management.usage.get(ident)['total'])

    def test_owner_sum_no_double_count(self):
        a,b = self.add('A','A'),self.add('B','B')
        owner = self.panel.management.metadata(a)['user_id']
        self.panel.management.edit(b, {'name':'B', 'user_id': owner})
        for ident in (a,b):
            self.panel.management.usage.sample(ident,'test','epoch',0,0)
            self.panel.management.usage.sample(ident,'test','epoch',50,70)
        user = next(u for u in self.panel.management.users() if u['id'] == owner)
        self.assertEqual(user['total'],240)
        self.assertEqual(set(user['devices']),{a,b})

    def test_unknown_owner_never_has_fake_zero(self):
        ident=self.add()
        owner=self.panel.management.metadata(ident)['user_id']
        user=next(u for u in self.panel.management.users() if u['id']==owner)
        self.assertIsNone(user['total'])
        self.assertIsNone(user['downloaded'])
        self.assertFalse(user['complete'])

    def test_labels_are_metadata_only_and_persist(self):
        ident=self.add('Before','Labels')
        p=self.store.get(ident)
        key=Path(p['keyfile']).read_bytes()
        self.panel.management.usage.sample(ident,'test','epoch',0,0)
        self.panel.management.usage.sample(ident,'test','epoch',100,50)
        usage=self.panel.management.usage.get(ident)
        with patch.object(self.panel.services,'action',side_effect=AssertionError('VPN service called')):
            self.panel.management.labels(ident,{'name':'Телефон','owner_name':'Тестовый владелец'})
        updated=self.store.get(ident)
        self.assertEqual({k:v for k,v in p.items() if k!='name'}, {k:v for k,v in updated.items() if k!='name'})
        self.assertEqual(Path(p['keyfile']).read_bytes(),key)
        self.assertEqual(self.panel.management.usage.get(ident),usage)
        new=Panel(Store(self.store.root),DemoServices(self.temp.name),True)
        self.assertEqual(new.store.get(ident)['name'],'Телефон')
        self.assertEqual(new.management.metadata(ident)['user_name'],'Тестовый владелец')
        new.management.labels(ident,{'name':'Только имя устройства'})
        self.assertEqual(new.management.metadata(ident)['user_name'],'Тестовый владелец')
        with self.assertRaises(PanelError):
            new.management.labels(ident,{'name':'Changed','owner_name':'Bad\nName'})
        self.assertEqual(new.store.get(ident)['name'],'Только имя устройства')

    def test_happ_labels_cannot_change_credentials_or_call_xray(self):
        ident='h-synthetic'
        with self.store.db() as db:
            db.execute('INSERT INTO happ_profiles VALUES (?,?,?,?,?)',(ident,'Before','synthetic-credential','inbound',''))
        self.panel.management.register(ident,'Owner','happ',True)
        with patch.object(self.panel.happ,'control',side_effect=AssertionError('Xray control called')), \
             patch.object(self.panel.happ,'persist',side_effect=AssertionError('Xray config written')):
            self.panel.management.labels(ident,{'name':'Laptop','owner_name':'Owner renamed'})
            with self.assertRaises(PanelError):
                self.panel.management.labels(ident,{'name':'Oops','flow':'xtls-rprx-vision'})
        self.assertEqual(self.panel.happ.get(ident)['credential'],'synthetic-credential')
        self.assertTrue(self.panel.management.metadata(ident)['enabled'])
        self.assertEqual(self.panel.happ.get(ident)['name'],'Laptop')

    def test_shared_owner_label_updates_all_devices(self):
        a,b=self.add('A','A'),self.add('B','B')
        owner=self.panel.management.metadata(a)['user_id']
        self.panel.management.edit(b,{'name':'B','user_id':owner})
        self.panel.management.labels(a,{'name':'A renamed','owner_name':'Shared owner'})
        self.assertEqual(self.panel.management.metadata(a)['user_name'],'Shared owner')
        self.assertEqual(self.panel.management.metadata(b)['user_name'],'Shared owner')
        self.assertEqual(self.store.get(b)['name'],'B')

    def test_delete_confirmation_retry_scope(self):
        a,b = self.add('A','A'),self.add('B','B')
        with self.assertRaises(PanelError):
            self.panel.management.delete_openflux(a, {'confirm_name':'wrong'})
        self.assertTrue(self.panel.management.delete_openflux(a, {'confirm_name':'A'})['ok'])
        self.assertTrue(self.panel.management.delete_openflux(a, {})['already_deleted'])
        self.assertTrue(Path(self.store.get(b)['keyfile']).exists())
        self.assertEqual(len(self.store.all()),1)

    def test_disabled_offline_distinct(self):
        a = self.add()
        self.panel.control(a, 'stop')
        self.assertFalse(self.panel.state()['profiles'][0]['access_enabled'])
        self.panel.control(a, 'start')
        self.panel.services.states[self.store.get(a)['unit']] = 'inactive'
        self.panel.sample()
        self.assertTrue(self.panel.state()['profiles'][0]['access_enabled'])
        self.assertEqual(self.panel.state()['profiles'][0]['state'],'inactive')

    def test_partial_delete_retry_and_no_reenable(self):
        ident=self.add()
        original=self.store.save_direct
        def failed(data):raise OSError('synthetic failure')
        self.store.save_direct=failed
        with self.assertRaises(OSError):
            self.panel.management.delete_openflux(ident,{'confirm_name':'test'})
        self.assertTrue(self.panel.management.metadata(ident)['deleting'])
        with self.assertRaises(PanelError):self.panel.control(ident,'start')
        self.store.save_direct=original
        self.assertTrue(self.panel.management.delete_openflux(ident,{'confirm_name':'test'})['ok'])

    def test_user_deletes_all_assigned_devices(self):
        a,b=self.add('A','A'),self.add('B','B')
        owner=self.panel.management.metadata(a)['user_id']
        self.panel.management.edit(b,{'name':'B','user_id':owner})
        self.panel.management.sync()
        self.assertEqual(len(self.panel.management.users()),1)
        self.assertTrue(self.panel.management.delete_user(owner,{'confirm_name':'A'})['ok'])
        self.assertEqual(self.store.all(),[])
        self.assertTrue(self.panel.management.delete_user(owner,{})['already_deleted'])

    def test_http_admin_and_favicon(self):
        web = WebServer(('127.0.0.1',0),Handler)
        web.panel,web.token = self.panel,'test-token'
        thread=threading.Thread(target=web.serve_forever,daemon=True)
        thread.start()
        def request(path,headers=None,method='GET',body=None):
            c=http.client.HTTPConnection('127.0.0.1',web.server_port)
            c.request(method,path,body=body,headers=headers or {})
            r=c.getresponse();result=(r.status,r.read());c.close();return result
        try:
            self.assertEqual(request('/api/state')[0],403)
            self.assertEqual(request('/api/state',{'X-Panel-Token':'test-token','Origin':'https://evil.test'})[0],403)
            self.assertEqual(request('/api/state',{'X-Panel-Token':'test-token','Host':'evil.test'})[0],403)
            self.assertEqual(request('/api/state',{'X-Panel-Token':'test-token'})[0],200)
            self.assertEqual(request('/api/profiles',{},'POST','{}')[0],403)
            self.assertEqual(request('/favicon.svg')[0],200)
            self.assertTrue(request('/favicon.png')[1].startswith(b'\x89PNG'))
            self.assertEqual(request('/mailru-polling.flux')[0], 404)
            self.assertEqual(request('/mailru-polling-author.pub')[0], 404)
            self.assertEqual(request('/mailru-polling.flux', {'Host':'evil.test'})[0], 403)
            html=request('/')[1].decode()
            self.assertNotIn('id="updated"',html)
            self.assertIn('nav-happ',html)
            self.assertIn('nav-amnezia',html)
            for suffix in ('start','stop','share','delete','edit','labels'):
                self.assertEqual(request('/api/amnezia/a-test/'+suffix,{},'POST','{}')[0],403)
            self.assertEqual(request('/api/amnezia',{},'POST','{}')[0],403)
            self.assertEqual(request('/api/amnezia/a-test/logs')[0],403)
            self.assertNotIn('id="owners"',html)
            self.assertNotIn('id="device-owner-name"',html)
            self.assertNotIn('id="device-owner"',html)
            self.assertNotIn('id="delete-confirm"',html)
            self.assertIn('Действительно удалить пользователя?',html)
            ident=self.add('HTTP labels','HTTP')
            body=json.dumps({'name':'Visual name','owner_name':'Visual owner'})
            path='/api/profiles/'+ident+'/labels'
            self.assertEqual(request(path,{},'POST',body)[0],403)
            with patch.object(self.panel.services,'action',side_effect=AssertionError('VPN action called')):
                self.assertEqual(request(path,{'X-Panel-Token':'test-token','Content-Type':'application/json'},'POST',body)[0],200)
            self.assertEqual(self.store.get(ident)['name'],'Visual name')
        finally:
            web.shutdown();web.server_close();thread.join()


if __name__=='__main__':
    unittest.main()
