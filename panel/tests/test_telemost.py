import base64,json,tempfile,unittest,zlib
from pathlib import Path
from unittest.mock import patch
from server import Panel,PanelError,Store,DemoServices,document_url,transport_for_url,service_text

URL='https://telemost.yandex.ru/j/12345678901234'

class TelemostTests(unittest.TestCase):
    def test_validation_and_normalization(self):
        self.assertEqual(document_url(' '+URL+'/', 'telemost'),URL)
        self.assertEqual(transport_for_url(URL),'telemost')
        for value in [URL+'?token=x',URL+'#x',URL+'/extra',URL.replace('https:','http:'),URL.replace('.ru/', '.ru.evil/'),URL.replace('/j/','/j/x'),URL+'\n--share']:
            with self.assertRaises(PanelError):document_url(value,'telemost')
        with self.assertRaises(PanelError):document_url(URL,'yandex')
    def test_service_no_direct_or_legacy(self):
        text=service_text(Path('/var/lib/openflux-panel-channels/m-123456789012'),URL)
        self.assertIn('--transport=telemost --negotiate',text)
        self.assertNotIn('--direct-',text)
        self.assertIn('MemoryMax=256M',text)
        self.assertIn('CPUQuota=70%',text)
        self.assertIn('cookies-telemost.json',text)
        with self.assertRaises(PanelError):service_text(Path('/tmp/synthetic'),URL,18445)
        with self.assertRaises(PanelError):service_text(Path('/tmp/synthetic'),URL,client_delivery='mailru-polling')
    def test_lifecycle_and_share_preserve_others(self):
        with tempfile.TemporaryDirectory() as tmp:
            panel=Panel(Store(Path(tmp)),DemoServices(tmp),True)
            old=panel.add({'name':'Mail original','url':'https://cloud.mail.ru/public/Test/Keep'})['id']
            old_key=Path(panel.store.get(old)['keyfile']).read_bytes();direct=panel.store.direct()
            with patch('yandex_document.check_yandex_document') as check:
                ident=panel.add({'name':'Telemost test','transport':'telemost','url':URL})['id']
                check.assert_not_called()
            with self.assertRaises(PanelError):panel.add({'name':'Duplicate','url':URL+'/'})
            self.assertEqual(panel.store.direct(),direct)
            share=panel.share(ident);encoded=share['link'].split('/v1/')[1]
            cfg=json.loads(zlib.decompress(base64.urlsafe_b64decode(encoded+'='*(-len(encoded)%4)),-15))
            self.assertEqual(cfg['transports'],[{'type':'telemost','url':URL}]);self.assertTrue(cfg['negotiate'])
            self.assertEqual(cfg['codec'],'batched');self.assertEqual(cfg['context'],URL)
            self.assertIsNone(share['direct_port'])
            panel.control(ident,'stop');self.assertFalse(panel.management.metadata(ident)['enabled'])
            panel.control(ident,'start');self.assertTrue(panel.management.metadata(ident)['enabled'])
            self.assertEqual(panel.share(ident)['secret'],share['secret'])
            panel.management.delete_openflux(ident,{'confirm_name':'Telemost test'})
            self.assertEqual(Path(panel.store.get(old)['keyfile']).read_bytes(),old_key)
            self.assertEqual(panel.store.direct(),direct)
    def test_logs_hide_auth(self):
        with tempfile.TemporaryDirectory() as tmp:
            panel=Panel(Store(Path(tmp)),DemoServices(tmp),True)
            ident=panel.add({'name':'Telemost test','url':URL})['id']
            with patch.object(panel.services,'logs',return_value='connected '+URL+'\ncredentials=DO_NOT_LEAK\n{"private":"DO_NOT_LEAK"}'):
                result=panel.logs(ident)['text']
            self.assertNotIn(URL,result);self.assertNotIn('DO_NOT_LEAK',result)

if __name__=='__main__':unittest.main()
