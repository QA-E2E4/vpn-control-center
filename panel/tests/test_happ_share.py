"""Regression: QR and copied link use the tested Happ fingerprint."""
import base64
import copy
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, unquote, urlsplit

from happ import Happ


class HappShareTests(unittest.TestCase):
    def test_firefox_link_and_qr_preserve_existing_parameters(self):
        import qrcode
        happ = object.__new__(Happ)
        credential = {'id': '00000000-0000-4000-8000-000000000001',
                      'flow': 'xtls-rprx-vision'}
        profile = {'name': 'Тест Wi-Fi', 'pending': '',
                   'credential': json.dumps(credential)}
        inbound = {'port': 8443, 'streamSettings': {'network': 'tcp',
                   'realitySettings': {'privateKey': 'synthetic-private',
                    'serverNames': ['example.com'], 'shortIds': ['0123456789abcdef']}}}
        original = copy.deepcopy(inbound)
        happ.panel = SimpleNamespace(management=SimpleNamespace(
            metadata=Mock(return_value={})))
        happ.store = SimpleNamespace(direct=Mock(return_value={'host': '192.0.2.1'}))
        happ.binary = Path('/synthetic/xray')
        happ.get = Mock(return_value=profile)
        happ.read = Mock(return_value=({}, inbound))
        happ.run = Mock(return_value='PublicKey: synthetic-public\n')
        with patch('qrcode.make', wraps=qrcode.make) as make:
            result = happ.share('synthetic')
        parsed = urlsplit(result['link'])
        self.assertEqual(parsed.username, credential['id'])
        self.assertEqual((parsed.hostname, parsed.port), ('192.0.2.1', 8443))
        self.assertEqual(unquote(parsed.fragment), profile['name'])
        self.assertEqual(parse_qs(parsed.query), {
            'encryption': ['none'], 'security': ['reality'], 'type': ['tcp'],
            'sni': ['example.com'], 'fp': ['firefox'], 'pbk': ['synthetic-public'],
            'sid': ['0123456789abcdef'], 'spx': ['/'], 'flow': ['xtls-rprx-vision']})
        self.assertEqual(make.call_args.args[0], result['link'])
        self.assertIn(b'<svg', base64.b64decode(result['svg'].split(',', 1)[1]))
        self.assertEqual(inbound, original)
        self.assertEqual(json.loads(profile['credential']), credential)


if __name__ == '__main__':
    unittest.main()
