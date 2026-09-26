"""Guard the LocalSend relay API.

Covers the prepare-upload metadata, the ticket parsing, the address defaults
and a full upload flow against a fake receiver.
"""
import importlib.util
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading
import types
import unittest

SOURCE = Path(__file__).resolve().parents[1] / 'localsend_send.py'

spec = importlib.util.spec_from_file_location('localsend_send', SOURCE)
sender = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sender)


class PrepareTests(unittest.TestCase):
    def test_the_metadata_carries_one_file(self):
        body, file_id = sender.prepare_body('nextcloud', 'FP', 53200, 'photo.jpg',
                                            123, '2026-09-13T00:00:00Z')
        self.assertEqual(file_id, 'file0')
        info = body['info']
        self.assertEqual(info['alias'], 'nextcloud')
        self.assertEqual(info['fingerprint'], 'FP')
        self.assertEqual(info['protocol'], 'http')
        self.assertFalse(info['download'])
        entry = body['files'][file_id]
        self.assertEqual(entry['fileName'], 'photo.jpg')
        self.assertEqual(entry['size'], 123)
        self.assertEqual(entry['fileType'], 'image/jpeg')

    def test_the_ticket_needs_a_session_and_a_token(self):
        payload = {'sessionId': 's', 'files': {'file0': 't'}}
        self.assertEqual(sender.parse_upload_ticket(payload, 'file0'), ('s', 't'))
        with self.assertRaises(ValueError):
            sender.parse_upload_ticket({'sessionId': 's', 'files': {}}, 'file0')

    def test_the_address_defaults_to_https(self):
        self.assertEqual(sender.device_address(
            {'protocol': 'https', 'port': 53317}, '192.0.2.5'),
            ('https', '192.0.2.5', 53317))
        self.assertEqual(sender.device_address(
            {'protocol': 'http'}, '192.0.2.5'), ('http', '192.0.2.5', 53317))
        self.assertEqual(sender.device_address({}, '192.0.2.5'),
                         ('https', '192.0.2.5', 53317))

    def test_our_own_fingerprint_is_ignored(self):
        self.assertTrue(sender.device_is_self({'fingerprint': 'ab'}, 'AB'))
        self.assertFalse(sender.device_is_self({'fingerprint': 'cd'}, 'AB'))


class FakeReceiver(BaseHTTPRequestHandler):
    received = {}

    def log_message(self, *args):
        pass

    def do_POST(self):
        length = int(self.headers.get('Content-Length') or 0)
        body = self.rfile.read(length)
        if self.path.startswith('/api/localsend/v2/prepare-upload'):
            FakeReceiver.received['prepare'] = json.loads(body)
            payload = json.dumps(
                {'sessionId': 'sess', 'files': {'file0': 'tok'}}).encode()
            self.send_response(200)
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        elif self.path.startswith('/api/localsend/v2/upload'):
            FakeReceiver.received['upload_url'] = self.path
            FakeReceiver.received['content'] = body
            self.send_response(200)
            self.send_header('Content-Length', '0')
            self.end_headers()
        else:
            self.send_response(404)
            self.end_headers()


class SendTests(unittest.TestCase):
    def test_the_upload_flow_reaches_the_receiver(self):
        FakeReceiver.received = {}
        server = ThreadingHTTPServer(('127.0.0.1', 0), FakeReceiver)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            device = {'alias': 'phone', 'fingerprint': 'dev',
                      'protocol': 'http', 'port': server.server_address[1]}
            result = sender.send_file(device, '127.0.0.1', 'note.txt', b'hello',
                                      'nextcloud', 'FP', 53200, timeout=10)
        finally:
            server.shutdown()
            server.server_close()
        self.assertEqual(result['status'], 'sent')
        prepare = FakeReceiver.received['prepare']
        self.assertEqual(prepare['files']['file0']['fileName'], 'note.txt')
        self.assertIn('sessionId=sess', FakeReceiver.received['upload_url'])
        self.assertIn('token=tok', FakeReceiver.received['upload_url'])
        self.assertEqual(FakeReceiver.received['content'], b'hello')

    def test_a_rejected_transfer_is_reported(self):
        class Rejecting(FakeReceiver):
            def do_POST(self):
                self.send_response(403)
                self.send_header('Content-Length', '0')
                self.end_headers()

        server = ThreadingHTTPServer(('127.0.0.1', 0), Rejecting)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            device = {'alias': 'phone', 'fingerprint': 'dev',
                      'protocol': 'http', 'port': server.server_address[1]}
            with self.assertRaises(RuntimeError):
                sender.send_file(device, '127.0.0.1', 'note.txt', b'hello',
                                 'nextcloud', 'FP', 53200, timeout=10)
        finally:
            server.shutdown()
            server.server_close()


class DeviceBookTests(unittest.TestCase):
    def test_devices_are_keyed_case_insensitively(self):
        book = sender.DeviceBook()
        book.remember({'fingerprint': 'AB', 'alias': 'phone'}, '192.0.2.5')
        self.assertIsNotNone(book.find('ab'))
        self.assertEqual(book.find('ab')['source_ip'], '192.0.2.5')

    def test_a_device_without_a_fingerprint_is_ignored(self):
        book = sender.DeviceBook()
        book.remember({'alias': 'phone'}, '192.0.2.5')
        self.assertEqual(book.all(), [])


class RegisterReceiver(BaseHTTPRequestHandler):
    received = {}

    def log_message(self, *args):
        pass

    def do_POST(self):
        length = int(self.headers.get('Content-Length') or 0)
        if self.path.startswith('/api/localsend/v2/register'):
            RegisterReceiver.received = json.loads(self.rfile.read(length))
            payload = json.dumps({
                'alias': 'phone', 'version': '2.2', 'deviceModel': 'Pixel',
                'deviceType': 'mobile', 'fingerprint': 'dev-fp', 'port': 53317,
                'protocol': 'http', 'download': False,
            }).encode()
            self.send_response(200)
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        else:
            self.send_response(404)
            self.end_headers()


class ScanDiscoveryTests(unittest.TestCase):
    def start_receiver(self):
        server = ThreadingHTTPServer(('127.0.0.1', 0), RegisterReceiver)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.shutdown)
        self.addCleanup(server.server_close)
        return server.server_address[1]

    def test_probing_an_address_returns_the_device(self):
        port = self.start_receiver()
        result = sender.probe_device('127.0.0.1', 'nextcloud', 'FP', 53200,
                                     'localsend-send', timeout=2, device_port=port)
        self.assertIsNotNone(result)
        address, info = result
        self.assertEqual(address, '127.0.0.1')
        self.assertEqual(info['alias'], 'phone')
        self.assertEqual(info['protocol'], 'http')
        self.assertEqual(RegisterReceiver.received['port'], 53200)
        self.assertEqual(RegisterReceiver.received['fingerprint'], 'FP')

    def test_a_scan_remembers_the_device(self):
        port = self.start_receiver()
        book = sender.DeviceBook()
        sender.scan_devices(book, 'nextcloud', 'FP', 53200, 'localsend-send',
                            '127.0.0.1/32', timeout=2, device_port=port)
        self.assertIsNotNone(book.find('dev-fp'))

    def test_the_scan_setting_can_be_disabled(self):
        self.assertIsNone(sender.resolve_scan_network('none'))
        self.assertIsNone(sender.resolve_scan_network(''))
        self.assertIsNone(sender.resolve_scan_network('0'))
        self.assertEqual(sender.resolve_scan_network('192.0.2.0/24'), '192.0.2.0/24')

    def test_the_registration_body_carries_our_port(self):
        body = sender.registration_body('nextcloud', 'FP', 53200, 'localsend-send')
        self.assertEqual(body['port'], 53200)
        self.assertEqual(body['fingerprint'], 'FP')
        self.assertFalse(body['download'])


class AuthorizationTests(unittest.TestCase):
    def test_only_the_shared_token_is_accepted(self):
        stub = types.SimpleNamespace(headers={'Authorization': 'Bearer s3cret'},
                                     server=types.SimpleNamespace(token='s3cret'))
        self.assertTrue(sender.SendHandler._authorized(stub))
        stub.headers = {'Authorization': 'Bearer wrong'}
        self.assertFalse(sender.SendHandler._authorized(stub))
        stub.headers = {}
        self.assertFalse(sender.SendHandler._authorized(stub))


class ConfigurationTests(unittest.TestCase):
    def test_the_token_and_fingerprint_must_be_set(self):
        os.environ.pop('LS_TEST_VAR', None)
        with self.assertRaises(SystemExit):
            sender.env_required('LS_TEST_VAR')
        os.environ['LS_TEST_VAR'] = '  value  '
        try:
            self.assertEqual(sender.env_required('LS_TEST_VAR'), 'value')
        finally:
            del os.environ['LS_TEST_VAR']


if __name__ == '__main__':
    unittest.main()
