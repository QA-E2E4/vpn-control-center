import base64
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import zlib

from server import Store, Panel, DemoServices, PanelError
from amnezia import CONFIG, TABLE, split_config, fields, identity


def key(n):
    return base64.b64encode(bytes([n])*32).decode()


class FakeAWG:
    def __init__(self):
        self.files = {CONFIG: '[Interface]\nPrivateKey = '+key(1)+'\nAddress = 10.8.1.0/24\nListenPort = 443\nJc = 4\nJmin = 10\nJmax = 50\nS1 = 117\nS2 = 63\nS3 = 28\nS4 = 15\nH1 = 10-20\nH2 = 30-40\nH3 = 50-60\nH4 = 70-80\n\n', TABLE: '[]'}
        self.live = {}; self.epoch = 'container-1:4'; self.n = 10
        self.fail = False

    def command(self, args, text=None):
        if args[0:2] == ['awg','genkey'] or args[0:2] == ['awg','genpsk']:
            self.n += 1; return key(self.n)
        if args[0:2] == ['awg','pubkey']:
            return base64.b64encode(hashlib.sha256(text.encode()).digest()).decode()
        if args[:2] != ['awg','set']: raise AssertionError(args)
        if self.fail: raise RuntimeError('synthetic runtime unavailable')
        public = args[4]
        if args[5] == 'remove': self.live.pop(public, None)
        else:
            self.live.setdefault(public, {'rx':0,'tx':0,'handshake':0})
            self.live[public].update({'allowed':args[6],'psk':text.strip() if text else '(none)'})
        return ''

    def write(self, path, previous, content):
        if previous != self.files[path]: raise RuntimeError('CAS conflict')
        self.files[path] = content

    def attach(self, adapter):
        adapter.command = self.command
        adapter.read_file = lambda path:self.files[path]
        adapter.write_file = self.write
        adapter.runtime = lambda:(self.epoch, self.live.copy())
        adapter.sample()


class AmneziaTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name))
        self.panel = Panel(self.store, DemoServices(self.temp.name), True)
        self.a = self.panel.amnezia
        self.fake = FakeAWG(); self.fake.attach(self.a)

    def tearDown(self): self.temp.cleanup()

    def add(self, name='Device'):
        return self.a.add({'name':name})['id']

    def test_create_export_identity_and_awg2_parameters(self):
        ident = self.add(); row = self.a.get(ident); share = self.a.share(ident)
        conf = share['config']
        self.assertIn('S3 = 28',conf); self.assertIn('S4 = 15',conf)
        self.assertIn('H1 = 10-20',conf)
        self.assertIn('PrivateKey = '+row['private_key'],conf)
        data = base64.urlsafe_b64decode(share['link'][6:]+'='*(-len(share['link'][6:])%4))
        raw = zlib.decompress(data[4:]); self.assertEqual(int.from_bytes(data[:4],'big'),len(raw))
        exported = json.loads(raw)
        self.assertEqual(json.loads(exported['containers'][0]['awg']['last_config'])['config'],conf)
        self.assertNotIn('private_key', json.dumps(self.a.state()))
        self.assertNotIn(row['private_key'],json.dumps(self.a.state()))
        self.assertFalse(self.a.info['device_binding'])

    def test_disable_enable_preserves_key_other_peer_and_restart(self):
        a,b = self.add('A'),self.add('B')
        private = self.a.get(a)['private_key']; other = self.a.get(b)['public_key']
        other_live = self.fake.live[other].copy()
        self.a.control(a,'stop')
        self.assertNotIn(self.a.get(a)['public_key'],split_config(self.fake.files[CONFIG])[2])
        self.assertEqual(self.fake.live[other],other_live)
        from amnezia import Amnezia
        again = Amnezia(self.panel); self.fake.attach(again)
        self.assertFalse(next(p for p in again.state() if p['id']==a)['access_enabled'])
        again.control(a,'start')
        self.assertEqual(again.get(a)['private_key'],private)
        self.assertEqual(self.fake.live[other],other_live)

    def test_traffic_direction_independence_and_counter_resets(self):
        a,b = self.add('A'),self.add('B'); public = self.a.get(a)['public_key']
        self.fake.live[public].update(rx=100,tx=500)
        self.a.sample(); self.a.sample()
        self.assertEqual(self.panel.management.usage.get(a)['downloaded'],500)
        self.assertEqual(self.panel.management.usage.get(a)['uploaded'],100)
        self.assertEqual(self.panel.management.usage.get(b)['total'],0)
        self.a.control(a,'stop'); self.a.control(a,'start')
        self.fake.live[public].update(rx=20,tx=40); self.a.sample()
        self.assertEqual(self.panel.management.usage.get(a)['total'],660)
        self.fake.epoch = 'container-2:4'; self.fake.live[public].update(rx=5,tx=10)
        self.a.sample(); self.assertEqual(self.panel.management.usage.get(a)['total'],675)

    def test_partial_delete_retry_and_no_reenable(self):
        ident=self.add(); public=self.a.get(ident)['public_key']
        self.fake.fail=True
        with self.assertRaises(PanelError):self.a.delete(ident,{'confirm_name':'Device'})
        self.assertNotIn(public,split_config(self.fake.files[CONFIG])[2])
        self.assertIn(public,self.fake.live)
        with self.assertRaises(PanelError):self.a.control(ident,'start')
        self.fake.fail=False
        self.a.delete(ident,{'confirm_name':'Device'})
        self.assertNotIn(public,self.fake.live)
        self.assertEqual(json.loads(self.fake.files[TABLE]),[])
        self.assertTrue(self.a.delete(ident,{})['already_deleted'])

    def test_existing_peer_adoption_no_rekey_no_fake_export(self):
        public=key(2)
        self.fake.files[CONFIG]+='[Peer]\nPublicKey = '+public+'\nPresharedKey = '+key(3)+'\nAllowedIPs = 10.8.1.5/32\n'
        self.fake.files[TABLE]=json.dumps([{'clientId':public,'userData':{'clientName':'Existing'}}])
        self.fake.live[public]={'psk':key(3),'allowed':'10.8.1.5/32','rx':10000,'tx':20000,'handshake':0}
        before=self.fake.files.copy(); self.a.sample()
        self.assertEqual(self.fake.files,before)
        self.assertEqual(self.a.get(identity(public))['name'],'Existing')
        self.assertEqual(self.panel.management.usage.get(identity(public))['total'],0)
        with self.assertRaises(PanelError):self.a.share(identity(public))

    def test_outage_keeps_totals_and_names_only_rename(self):
        ident=self.add(); public=self.a.get(ident)['public_key']
        self.fake.live[public].update(rx=10,tx=20); self.a.sample()
        before=self.fake.files.copy()
        self.panel.management.labels(ident,{'name':'Renamed'})
        self.assertEqual(self.fake.files,before)
        self.a.runtime=lambda:(_ for _ in ()).throw(RuntimeError('offline'))
        self.a.sample(); usage=self.panel.management.usage.get(ident)
        self.assertEqual(usage['total'],30);self.assertFalse(usage['available'])
        self.assertFalse(self.a.ready)

    def test_ipv4_allocation_reserves_disabled_peers(self):
        a=self.add(); address=json.loads(self.a.get(a)['peer'])['AllowedIPs']
        self.a.control(a,'stop'); b=self.add()
        self.assertNotEqual(address,json.loads(self.a.get(b)['peer'])['AllowedIPs'])

    def test_options_validation_and_no_server_changes(self):
        ident=self.add(); before=self.fake.files.copy()
        self.a.edit(ident,{'dns':'8.8.8.8','mtu':1400})
        self.assertIn('MTU = 1400',self.a.share(ident)['config'])
        self.assertEqual(before,self.fake.files)
        with self.assertRaises(ValueError):self.a.edit(ident,{'dns':'1.1.1.1\nPostUp=evil','mtu':1280})


if __name__=='__main__':unittest.main()
