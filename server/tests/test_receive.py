"""Exercise the relay's LocalSend receiver against a fake Nextcloud."""
import importlib.util
import json
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading
import unittest
from urllib.parse import unquote

SOURCE = Path(__file__).resolve().parents[1] / 'localsend_send.py'
spec = importlib.util.spec_from_file_location('localsend_send_receive', SOURCE)
relay = importlib.util.module_from_spec(spec)
spec.loader.exec_module(relay)

TOKEN = 'relay-token'


class FakeNextcloud(BaseHTTPRequestHandler):
    """Answers /receive/config and records /receive/file uploads."""
    config = {}
    stored = []
    status = 200

    def log_message(self, *args):
        pass

    def _send(self, status, payload):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.headers.get('Authorization') != 'Bearer ' + TOKEN:
            self._send(401, {'error': 'unauthorized'})
        elif self.path.endswith('/receive/config'):
            self._send(200, FakeNextcloud.config)
        else:
            self._send(404, {})

    def do_PUT(self):
        length = int(self.headers.get('Content-Length') or 0)
        body = self.rfile.read(length)
        if self.headers.get('Authorization') != 'Bearer ' + TOKEN:
            self._send(401, {'error': 'unauthorized'})
            return
        FakeNextcloud.stored.append({
            'path': self.path,
            'name': unquote(self.headers.get('X-Receive-Filename')),
            'sender': unquote(self.headers.get('X-Receive-Sender')),
            'body': body,
        })
        self._send(FakeNextcloud.status, {'status': 'stored'})


def serve(handler, configure=None):
    server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
    if configure:
        configure(server)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


class ReceiverTests(unittest.TestCase):
    def setUp(self):
        FakeNextcloud.config = {'enabled': True, 'pin': '2468', 'maxBytes': 100,
                                'maxConcurrent': 1}
        FakeNextcloud.stored = []
        FakeNextcloud.status = 200
        self.nextcloud = serve(FakeNextcloud)
        self.addCleanup(self.nextcloud.server_close)
        self.addCleanup(self.nextcloud.shutdown)

        def configure(server):
            server.token = TOKEN
            server.fingerprint = 'relay-fp'
            server.port = 53200
            server.alias = 'nextcloud'
            server.model = 'localsend-send'
            server.book = relay.DeviceBook()
            server.nextcloud = relay.NextcloudLink(
                f'http://127.0.0.1:{self.nextcloud.server_address[1]}/', TOKEN)
            relay.CONFIG_CACHE_SECONDS = 0
            server.sessions = relay.ReceiveSessions()

        self.relay = serve(relay.SendHandler, configure)
        self.addCleanup(self.relay.server_close)
        self.addCleanup(self.relay.shutdown)

    def post(self, path, body):
        client = HTTPConnection('127.0.0.1', self.relay.server_address[1], timeout=10)
        try:
            data = body if isinstance(body, bytes) else json.dumps(body).encode()
            client.request('POST', path, body=data)
            response = client.getresponse()
            payload = response.read()
            return response.status, json.loads(payload) if payload else {}
        finally:
            client.close()

    def prepare(self, pin=None, files=None):
        request = {
            'info': {'alias': 'Pixel', 'fingerprint': 'phone'},
            'files': files or {'f1': {'id': 'f1', 'fileName': 'photo.jpg', 'size': 5}},
        }
        path = '/api/localsend/v2/prepare-upload'
        if pin is not None:
            path += '?pin=' + pin
        return self.post(path, request)

    def test_a_missing_or_wrong_pin_is_answered_with_401(self):
        self.assertEqual(self.prepare()[0], 401)
        self.assertEqual(self.prepare('1111')[0], 401)
        self.assertEqual(FakeNextcloud.stored, [])

    def test_the_right_pin_lets_a_file_reach_nextcloud(self):
        status, ticket = self.prepare('2468')
        self.assertEqual(status, 200)
        token = ticket['files']['f1']
        status, _ = self.post('/api/localsend/v2/upload?sessionId='
                              f'{ticket["sessionId"]}&fileId=f1&token={token}', b'hello')
        self.assertEqual(status, 200)
        self.assertEqual(len(FakeNextcloud.stored), 1)
        stored = FakeNextcloud.stored[0]
        self.assertTrue(stored['path'].endswith('/apps/localsend_share/receive/file'))
        self.assertEqual(stored['name'], 'photo.jpg')
        self.assertEqual(stored['sender'], 'Pixel')
        self.assertEqual(stored['body'], b'hello')
        # The token is used up once the file is stored.
        status, _ = self.post('/api/localsend/v2/upload?sessionId='
                              f'{ticket["sessionId"]}&fileId=f1&token={token}', b'hello')
        self.assertEqual(status, 403)

    def test_an_upload_with_a_wrong_token_is_refused(self):
        _, ticket = self.prepare('2468')
        status, _ = self.post('/api/localsend/v2/upload?sessionId='
                              f'{ticket["sessionId"]}&fileId=f1&token=guess', b'hello')
        self.assertEqual(status, 403)
        self.assertEqual(FakeNextcloud.stored, [])

    def test_receiving_turned_off_in_nextcloud_refuses_everything(self):
        FakeNextcloud.config = {'enabled': False}
        self.assertEqual(self.prepare('2468')[0], 403)

    def test_an_unreachable_nextcloud_means_closed(self):
        self.nextcloud.shutdown()
        self.nextcloud.server_close()
        self.assertEqual(self.prepare('2468')[0], 403)

    def test_files_over_the_size_limit_are_refused(self):
        files = {'big': {'id': 'big', 'fileName': 'big.iso', 'size': 101}}
        self.assertEqual(self.prepare('2468', files)[0], 413)

    def test_the_concurrency_limit_blocks_a_second_session(self):
        self.assertEqual(self.prepare('2468')[0], 200)
        self.assertEqual(self.prepare('2468')[0], 409)

    def test_cancel_frees_the_slot(self):
        _, ticket = self.prepare('2468')
        self.post(f'/api/localsend/v2/cancel?sessionId={ticket["sessionId"]}', b'')
        self.assertEqual(self.prepare('2468')[0], 200)

    def test_a_body_that_does_not_match_the_announced_size_is_refused(self):
        _, ticket = self.prepare('2468')
        status, _ = self.post('/api/localsend/v2/upload?sessionId='
                              f'{ticket["sessionId"]}&fileId=f1'
                              f'&token={ticket["files"]["f1"]}', b'hi')
        self.assertEqual(status, 400)
        self.assertEqual(FakeNextcloud.stored, [])

    def test_a_storage_failure_is_reported_and_the_file_can_be_retried(self):
        FakeNextcloud.status = 500
        _, ticket = self.prepare('2468')
        url = (f'/api/localsend/v2/upload?sessionId={ticket["sessionId"]}'
               f'&fileId=f1&token={ticket["files"]["f1"]}')
        self.assertEqual(self.post(url, b'hello')[0], 500)
        FakeNextcloud.status = 200
        self.assertEqual(self.post(url, b'hello')[0], 200)


class PinLockoutTests(unittest.TestCase):
    def test_repeated_wrong_pins_lock_the_address_out_for_a_while(self):
        now = [1000.0]
        sessions = relay.ReceiveSessions(clock=lambda: now[0])
        config = {'enabled': True, 'pin': '2468', 'maxConcurrent': 5}
        request = {'files': {'f': {'fileName': 'a', 'size': 1}}}
        for _ in range(relay.PIN_ATTEMPTS):
            self.assertEqual(sessions.prepare(config, request, '0000', '192.0.2.9')[0], 401)
        self.assertEqual(sessions.prepare(config, request, '2468', '192.0.2.9')[0], 429)
        # Other addresses are not affected.
        self.assertEqual(sessions.prepare(config, request, '2468', '192.0.2.10')[0], 200)
        now[0] += relay.PIN_WINDOW_SECONDS + 1
        self.assertEqual(sessions.prepare(config, request, '2468', '192.0.2.9')[0], 200)

    def test_a_config_without_a_pin_never_accepts(self):
        sessions = relay.ReceiveSessions()
        request = {'files': {'f': {'fileName': 'a', 'size': 1}}}
        self.assertEqual(sessions.prepare({'enabled': True, 'pin': ''}, request, '', 'x')[0], 403)

    def test_idle_sessions_expire(self):
        now = [0.0]
        sessions = relay.ReceiveSessions(clock=lambda: now[0])
        config = {'enabled': True, 'pin': '2468', 'maxConcurrent': 1}
        request = {'files': {'f': {'fileName': 'a', 'size': 1}}}
        self.assertEqual(sessions.prepare(config, request, '2468', 'x')[0], 200)
        now[0] += relay.SESSION_IDLE_SECONDS + 1
        self.assertEqual(sessions.prepare(config, request, '2468', 'y')[0], 200)


class RelayWithoutReceivingTests(unittest.TestCase):
    def test_prepare_upload_is_refused_when_no_nextcloud_url_is_set(self):
        def configure(server):
            server.token = TOKEN
        server = serve(relay.SendHandler, configure)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        client = HTTPConnection('127.0.0.1', server.server_address[1], timeout=10)
        client.request('POST', '/api/localsend/v2/prepare-upload?pin=1234', body=b'{}')
        self.assertEqual(client.getresponse().status, 403)
        client.close()


if __name__ == '__main__':
    unittest.main()
