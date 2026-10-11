import io,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch,MagicMock
from yandex_document import check_yandex_document,classify,DocumentCheckError,LIMIT
from server import Panel,Store,DemoServices,PanelError

def page(edit=True):
    cfg={'officeActionData':{'balancer_url':'https://example.onlyoffice.disk.yandex.net','editor_config':{'token':'synthetic','document':{'key':'synthetic','permissions':{'edit':edit}}}}}
    return '<script id="client-config">'+json.dumps(cfg)+'</script>'

class Response(io.BytesIO):
    def __init__(self,status=200,body='',location=None):
        super().__init__(body.encode());self.status=status;self.headers={'Location':location} if location else {}

class DocumentTests(unittest.TestCase):
    def test_ready_does_not_claim_vpn(self):self.assertEqual(classify(page()),{'status':'editor_metadata_ready','vpn_verified':False})
    def test_viewer_is_not_login(self):
        with self.assertRaises(DocumentCheckError) as e:classify('<script id="store-prefetch">{}</script><a href="https://passport.yandex.ru">Login</a>')
        self.assertEqual(e.exception.code,'viewer')
    def test_readonly(self):
        with self.assertRaises(DocumentCheckError) as e:classify(page(False))
        self.assertEqual(e.exception.code,'readonly')
    def test_bad_and_volga_config(self):
        for body in ['<script id="client-config">[]</script>','<script id="client-config">{"officeActionData":{"officeType":"volga"}}</script>',page().replace('"token": "synthetic"','"token": ""')]:
            with self.assertRaises(DocumentCheckError):classify(body)
    def test_redirect_to_ready(self):
        op=MagicMock();op.open.side_effect=[Response(302,location='https://docs.yandex.ru/edit/d/Synthetic'),Response(body=page())]
        with patch('yandex_document.build_opener',return_value=op):self.assertEqual(check_yandex_document('https://disk.yandex.ru/i/Synthetic')['status'],'editor_metadata_ready')
        self.assertEqual(op.open.call_count,2)
    def test_auth_and_unsafe_redirects_not_followed(self):
        cases=[('https://passport.yandex.ru/auth?token=DO_NOT_LEAK','login'),('https://docs.yandex.ru/showcaptcha?secret=DO_NOT_LEAK','captcha'),('http://127.0.0.1/','unavailable'),('https://docs.yandex.ru.attacker.invalid/','unavailable'),('https://docs.yandex.ru:8443/','unavailable')]
        for url,expected in cases:
            op=MagicMock();op.open.return_value=Response(302,location=url)
            with patch('yandex_document.build_opener',return_value=op),self.assertRaises(DocumentCheckError) as e:check_yandex_document('https://disk.yandex.ru/i/Synthetic')
            self.assertEqual(e.exception.code,expected);self.assertNotIn('DO_NOT_LEAK',str(e.exception));self.assertEqual(op.open.call_count,1)
    def test_status_size_timeout_and_initial_scope(self):
        for response,expected in [(Response(403),'denied'),(Response(503),'unavailable'),(Response(body='x'*(LIMIT+1)),'unavailable')]:
            op=MagicMock();op.open.return_value=response
            with patch('yandex_document.build_opener',return_value=op),self.assertRaises(DocumentCheckError) as e:check_yandex_document('https://disk.yandex.ru/i/Synthetic')
            self.assertEqual(e.exception.code,expected)
        op=MagicMock();op.open.side_effect=TimeoutError('private-url DO_NOT_LEAK')
        with patch('yandex_document.build_opener',return_value=op),self.assertRaises(DocumentCheckError) as e:check_yandex_document('https://disk.yandex.ru/i/Synthetic')
        self.assertNotIn('DO_NOT_LEAK',str(e.exception))
        with patch('yandex_document.build_opener') as build,self.assertRaises(DocumentCheckError):check_yandex_document('http://127.0.0.1/')
        build.assert_not_called()
    def test_rejected_creation_has_no_side_effects(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=Store(Path(tmp));panel=Panel(store,DemoServices(tmp),True);panel.demo=False
            before=store.direct()
            with patch('server.os.access',return_value=True),patch('yandex_document.check_yandex_document',side_effect=DocumentCheckError('viewer')),patch.object(panel.services,'provision') as provision,self.assertRaises(PanelError):
                panel.add({'name':'Probe','url':'https://disk.yandex.ru/i/Synthetic'})
            self.assertEqual(store.all(),[]);self.assertEqual(store.direct(),before);provision.assert_not_called()
    def test_mail_does_not_use_yandex_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            panel=Panel(Store(Path(tmp)),DemoServices(tmp),True);panel.demo=False
            with patch('server.os.access',return_value=True),patch('yandex_document.check_yandex_document') as check,patch.object(panel.services,'provision'),patch.object(panel.services,'action'):
                panel.add({'name':'Mail','url':'https://cloud.mail.ru/public/Test/Unchanged'})
            check.assert_not_called()

if __name__=='__main__':unittest.main()
