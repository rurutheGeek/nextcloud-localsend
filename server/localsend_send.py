#!/usr/bin/env python3
"""LocalSend relay for the localsend_share Nextcloud app. Standard library only.

This service implements the sending half of LocalSend protocol v2: it
discovers devices on the LAN (UDP multicast/broadcast plus the HTTP register
endpoint), then sends a file to the chosen device's LocalSend app
(prepare-upload + upload).

The Nextcloud app calls this API:
  GET  /devices                   -> discovered devices (alias, fingerprint, address)
  POST /send                      -> headers: X-Send-To (fingerprint),
                                     X-Send-Filename, X-Send-User;
                                     body: the file bytes
  POST /api/localsend/v2/register -> discovery replies from LocalSend devices
  GET  /healthz

Anything else that can speak HTTP can use /devices and /send as well.

Optional receiving (LOCALSEND_RECEIVE_NEXTCLOUD_URL set): the relay also
answers the LocalSend receiver endpoints (info, prepare-upload, upload,
cancel) on its port and on LOCALSEND_RECEIVE_PORT. The Nextcloud app decides
whether receiving is on, the PIN and the limits; accepted files are streamed
to Nextcloud and nothing is kept on the relay. prepare-upload without the
right PIN answers 401, so LocalSend apps ask the sender for it.

Environment:
  LOCALSEND_SEND_TOKEN        shared secret (required)
  LOCALSEND_SEND_FINGERPRINT  stable device fingerprint (required; generate once
                              with e.g. `openssl rand -hex 32`)
  LOCALSEND_SEND_PORT         default 53200
  LOCALSEND_SEND_ALIAS        sender name shown on the receiving device,
                              default nextcloud
  LOCALSEND_SEND_TIMEOUT      discovery wait in seconds, default 3
  LOCALSEND_SEND_MAX_BYTES    default 2 GiB
  LOCALSEND_SEND_SCAN         CIDR to probe for devices (HTTP legacy discovery,
                              the protocol's fallback when multicast is not
                              possible). Default: the local /24. Set to "none"
                              to only use multicast.
  LOCALSEND_RECEIVE_NEXTCLOUD_URL  base URL of the Nextcloud with the app
                              (e.g. https://cloud.example.net); turns the
                              receiver on. Uses LOCALSEND_SEND_TOKEN.
  LOCALSEND_RECEIVE_PORT      extra listen port for LocalSend's HTTP scan,
                              default 53317; "none" to disable
  LOCALSEND_RECEIVE_INSECURE  "1" to skip TLS verification towards Nextcloud

Multicast discovery needs the devices to reach our HTTP port, which firewalls
often block. The scan probes every address in LOCALSEND_SEND_SCAN outbound, so
it also works when inbound traffic is restricted.
"""
import concurrent.futures
import hmac
import ipaddress
import json
import mimetypes
import os
import secrets
import socket
import ssl
import threading
import time
from datetime import datetime, timezone
from http.client import HTTPConnection, HTTPSConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, unquote, urlsplit

MULTICAST = ('224.0.0.167', 53317)
BROADCAST = ('255.255.255.255', 53317)
PROTOCOL_VERSION = '2.2'
DEVICE_PORT = 53317
SCAN_CONCURRENCY = 64


def device_is_self(device, fingerprint):
    return (device.get('fingerprint') or '').lower() == fingerprint.lower()


def device_address(device, source_ip):
    """Return (scheme, host, port) for a discovered device."""
    scheme = 'https' if (device.get('protocol') or 'https') == 'https' else 'http'
    port = int(device.get('port') or 53317)
    return scheme, source_ip, port


def prepare_body(alias, fingerprint, port, filename, size, modified, model='localsend-send'):
    mime, _ = mimetypes.guess_type(filename)
    file_id = 'file0'
    return {
        'info': {
            'alias': alias,
            'version': PROTOCOL_VERSION,
            'deviceModel': model,
            'deviceType': 'server',
            'fingerprint': fingerprint,
            'port': port,
            'protocol': 'http',
            'download': False,
        },
        'files': {
            file_id: {
                'id': file_id,
                'fileName': filename,
                'size': size,
                'fileType': mime or 'application/octet-stream',
                'sha256': None,
                'metadata': {'modified': modified},
            },
        },
    }, file_id


def parse_upload_ticket(payload, file_id):
    """Pull the session and token out of the prepare-upload response."""
    session = payload.get('sessionId')
    tokens = payload.get('files') or {}
    token = tokens.get(file_id)
    if not session or not token:
        raise ValueError('prepare-upload did not return a session and token')
    return session, token


def modified_time(path=None):
    when = datetime.fromtimestamp(
        os.path.getmtime(path), tz=timezone.utc) if path else datetime.now(timezone.utc)
    return when.strftime('%Y-%m-%dT%H:%M:%SZ')


def http_client(scheme, host, port, timeout):
    if scheme == 'https':
        # LocalSend devices use self-signed certificates and trust each other
        # by fingerprint, so certificate verification is off here as well.
        context = ssl._create_unverified_context()
        return HTTPSConnection(host, port, context=context, timeout=timeout)
    return HTTPConnection(host, port, timeout=timeout)


def send_file(device, source_ip, filename, content, alias, fingerprint, api_port,
              model='localsend-send', timeout=120):
    """Run the LocalSend v2 upload flow and return the receiving device's answer."""
    scheme, host, port = device_address(device, source_ip)
    body, file_id = prepare_body(alias, fingerprint, api_port, filename,
                                 len(content), modified_time(), model)
    client = http_client(scheme, host, port, timeout)
    try:
        client.request('POST', '/api/localsend/v2/prepare-upload',
                       body=json.dumps(body).encode(),
                       headers={'Content-Type': 'application/json'})
        response = client.getresponse()
        payload = response.read()
        if response.status == 204:
            return {'status': 'skipped', 'device': device.get('alias')}
        if response.status != 200:
            raise RuntimeError(f'prepare-upload failed: {response.status} '
                               f'{payload[:200].decode(errors="replace")}')
        session, token = parse_upload_ticket(json.loads(payload), file_id)
        client.request('POST',
                       f'/api/localsend/v2/upload?sessionId={session}'
                       f'&fileId={file_id}&token={token}',
                       body=content,
                       headers={'Content-Type': 'application/octet-stream'})
        response = client.getresponse()
        response.read()
        if response.status != 200:
            raise RuntimeError(f'upload failed: {response.status}')
        return {'status': 'sent', 'device': device.get('alias'),
                'session': session, 'bytes': len(content)}
    finally:
        client.close()


class DeviceBook:
    """Recently discovered devices, keyed by fingerprint."""

    def __init__(self):
        self.lock = threading.Lock()
        self.devices = {}

    def remember(self, info, source_ip):
        fingerprint = (info.get('fingerprint') or '').lower()
        if not fingerprint:
            return
        with self.lock:
            self.devices[fingerprint] = dict(info, source_ip=source_ip)

    def find(self, fingerprint):
        with self.lock:
            return self.devices.get((fingerprint or '').lower())

    def all(self):
        with self.lock:
            return list(self.devices.values())


def announce_message(fingerprint, alias, api_port, model='localsend-send'):
    return json.dumps({
        'alias': alias,
        'version': PROTOCOL_VERSION,
        'deviceModel': model,
        'deviceType': 'server',
        'fingerprint': fingerprint,
        'port': api_port,
        'protocol': 'http',
        'download': False,
        'announce': True,
    }).encode()


def registration_body(alias, fingerprint, api_port, model):
    """Our own info, as sent by /register (legacy discovery)."""
    return {
        'alias': alias,
        'version': PROTOCOL_VERSION,
        'deviceModel': model,
        'deviceType': 'server',
        'fingerprint': fingerprint,
        'port': api_port,
        'protocol': 'http',
        'download': False,
    }


def local_ipv4():
    """The address of the interface towards the default route."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(('192.0.2.1', 9))
        return sock.getsockname()[0]
    except OSError:
        return None
    finally:
        sock.close()


def default_scan_network():
    address = local_ipv4()
    if not address:
        return None
    try:
        return str(ipaddress.ip_network(address + '/24', strict=False))
    except ValueError:
        return None


def resolve_scan_network(value):
    """Interpret LOCALSEND_SEND_SCAN, falling back to the local /24."""
    if value is None:
        return default_scan_network()
    value = value.strip()
    if value.lower() in ('', 'none', 'off', '0'):
        return None
    return value


def probe_device(address, alias, fingerprint, api_port, model,
                 timeout=0.8, device_port=DEVICE_PORT):
    """Ask one address for its LocalSend info. Returns (address, info) or None."""
    body = json.dumps(registration_body(alias, fingerprint, api_port, model)).encode()
    for scheme in ('https', 'http'):
        try:
            client = http_client(scheme, address, device_port, timeout)
            try:
                client.request('POST', '/api/localsend/v2/register', body=body,
                               headers={'Content-Type': 'application/json'})
                response = client.getresponse()
                payload = response.read()
                if response.status == 200:
                    info = json.loads(payload) if payload else {}
                    if isinstance(info, dict) and not device_is_self(info, fingerprint):
                        info.setdefault('port', device_port)
                        info['protocol'] = scheme
                        info['fingerprint'] = (info.get('fingerprint')
                                               or f'{address}:{device_port}')
                        return address, info
            finally:
                client.close()
        except Exception:  # noqa: BLE001 - unreachable/mismatched protocol
            continue
    return None


def scan_devices(book, alias, fingerprint, api_port, model, network, timeout,
                 device_port=DEVICE_PORT):
    """Probe every host in the network. Outbound only, so firewalls do not matter."""
    try:
        hosts = [str(host) for host in ipaddress.ip_network(network, strict=False).hosts()]
    except ValueError:
        return
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=SCAN_CONCURRENCY)
    try:
        futures = [
            pool.submit(probe_device, host, alias, fingerprint, api_port, model,
                        min(0.8, timeout), device_port)
            for host in hosts
        ]
        try:
            for future in concurrent.futures.as_completed(futures, timeout=timeout):
                try:
                    result = future.result()
                except Exception:  # noqa: BLE001 - one host must not stop the scan
                    continue
                if result:
                    book.remember(result[1], result[0])
        except concurrent.futures.TimeoutError:
            pass
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


def discover(book, fingerprint, alias, api_port, timeout, model='localsend-send', scan=None):
    """Find devices: multicast announce plus an outbound scan of the LAN."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.bind(('', 0))
    sock.settimeout(0.5)
    message = announce_message(fingerprint, alias, api_port, model)
    for _ in range(2):
        for target in (MULTICAST, BROADCAST):
            try:
                sock.sendto(message, target)
            except OSError:
                pass
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            payload, address = sock.recvfrom(65535)
        except socket.timeout:
            continue
        try:
            info = json.loads(payload)
        except ValueError:
            continue
        if not isinstance(info, dict) or device_is_self(info, fingerprint):
            continue
        book.remember(info, address[0])
    sock.close()
    if scan:
        scan_devices(book, alias, fingerprint, api_port, model, scan, timeout)
    return book.all()


def public_device(record):
    return {
        'alias': record.get('alias') or record.get('fingerprint'),
        'fingerprint': record.get('fingerprint'),
        'address': record.get('source_ip'),
        'port': record.get('port') or 53317,
        'protocol': record.get('protocol') or 'https',
        'deviceType': record.get('deviceType') or 'desktop',
    }


def read_body(handler, length):
    """Read a fixed-length or chunked request body."""
    if handler.headers.get('Transfer-Encoding', '').lower() == 'chunked':
        chunks = []
        while True:
            size_line = handler.rfile.readline().split(b';')[0].strip()
            size = int(size_line or b'0', 16)
            if size == 0:
                handler.rfile.readline()
                break
            chunks.append(handler.rfile.read(size))
            handler.rfile.read(2)
        return b''.join(chunks)
    return handler.rfile.read(length) if length > 0 else b''


# --- Receiving (optional) -------------------------------------------------
#
# With LOCALSEND_RECEIVE_NEXTCLOUD_URL set, the relay also acts as a LocalSend
# receiver. Whether it accepts anything, the PIN and the limits are decided by
# the Nextcloud app (GET <nextcloud>/receive/config); accepted files are
# streamed to Nextcloud (POST <nextcloud>/receive/file), which stores them in
# the user and folder the administrator chose. Nothing is kept on the relay.

RECEIVE_ROUTE = '/index.php/apps/localsend_share/receive'
CONFIG_CACHE_SECONDS = 10
SESSION_IDLE_SECONDS = 30 * 60
PIN_ATTEMPTS = 5
PIN_WINDOW_SECONDS = 5 * 60
DISABLED_CONFIG = {'enabled': False}


class NextcloudLink:
    """Talks to the localsend_share app: receive settings and file delivery."""

    def __init__(self, base_url, token, insecure=False, timeout=30):
        self.base_url = base_url.rstrip('/')
        self.token = token
        self.insecure = insecure
        self.timeout = timeout
        self.lock = threading.Lock()
        self.cached = None
        self.cached_at = 0.0

    def _connection(self, timeout):
        parts = urlsplit(self.base_url)
        host = parts.hostname
        if parts.scheme == 'https':
            context = (ssl._create_unverified_context() if self.insecure
                       else ssl.create_default_context())
            return HTTPSConnection(host, parts.port or 443, context=context,
                                   timeout=timeout), parts.path
        return HTTPConnection(host, parts.port or 80, timeout=timeout), parts.path

    def config(self):
        """The receive settings, cached briefly. Any failure means 'disabled'."""
        with self.lock:
            if self.cached is not None and time.monotonic() - self.cached_at < CONFIG_CACHE_SECONDS:
                return self.cached
        try:
            client, prefix = self._connection(10)
            try:
                client.request('GET', prefix + RECEIVE_ROUTE + '/config',
                               headers={'Authorization': 'Bearer ' + self.token,
                                        'Accept': 'application/json'})
                response = client.getresponse()
                payload = response.read()
            finally:
                client.close()
            config = json.loads(payload) if response.status == 200 else DISABLED_CONFIG
            if not isinstance(config, dict):
                config = DISABLED_CONFIG
        except Exception as error:  # noqa: BLE001 - unreachable Nextcloud = closed
            print(f'receive: could not load the settings: {error}', flush=True)
            config = DISABLED_CONFIG
        with self.lock:
            self.cached, self.cached_at = config, time.monotonic()
        return config

    def deliver(self, filename, sender, size, stream):
        """Stream one file to Nextcloud. Returns (status, payload)."""
        client, prefix = self._connection(max(self.timeout, 300))
        try:
            client.request('PUT', prefix + RECEIVE_ROUTE + '/file', body=stream,
                           headers={'Authorization': 'Bearer ' + self.token,
                                    'Content-Type': 'application/octet-stream',
                                    'Content-Length': str(size),
                                    'X-Receive-Filename': quote(filename, safe=''),
                                    'X-Receive-Sender': quote(sender, safe='')},
                           encode_chunked=False)
            response = client.getresponse()
            payload = response.read()
        finally:
            client.close()
        try:
            return response.status, json.loads(payload)
        except ValueError:
            return response.status, {}


class LimitedReader:
    """Read at most `remaining` bytes from a stream (the request body)."""

    def __init__(self, stream, remaining):
        self.stream = stream
        self.remaining = remaining

    def read(self, size=-1):
        if self.remaining <= 0:
            return b''
        if size is None or size < 0 or size > self.remaining:
            size = self.remaining
        chunk = self.stream.read(min(size, 1024 * 1024))
        self.remaining -= len(chunk)
        return chunk


class ReceiveSessions:
    """prepare-upload sessions, the PIN check and the concurrency limit."""

    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.lock = threading.Lock()
        self.sessions = {}
        self.failures = {}

    def _expire(self, now):
        for session_id in [key for key, session in self.sessions.items()
                           if now - session['touched'] > SESSION_IDLE_SECONDS]:
            del self.sessions[session_id]

    def _locked_out(self, ip, now):
        recent = [at for at in self.failures.get(ip, []) if now - at < PIN_WINDOW_SECONDS]
        self.failures[ip] = recent
        return len(recent) >= PIN_ATTEMPTS

    def prepare(self, config, request, pin, ip):
        """Validate a prepare-upload request. Returns (status, payload)."""
        if not config.get('enabled'):
            return 403, {'message': 'Receiving is turned off'}
        expected = str(config.get('pin') or '')
        if not expected:
            return 403, {'message': 'Receiving is turned off'}
        now = self.clock()
        with self.lock:
            self._expire(now)
            if self._locked_out(ip, now):
                return 429, {'message': 'Too many wrong PINs, try again later'}
            if not hmac.compare_digest((pin or '').encode(), expected.encode()):
                # The official app asks for the PIN on 401 and retries with ?pin=.
                if pin:
                    self.failures.setdefault(ip, []).append(now)
                return 401, {'message': 'PIN required'}
            self.failures.pop(ip, None)
            files = request.get('files') if isinstance(request, dict) else None
            if not isinstance(files, dict) or not files:
                return 400, {'message': 'No files'}
            limit = int(config.get('maxBytes') or 0)
            accepted = {}
            for file_id, entry in files.items():
                if not isinstance(entry, dict):
                    return 400, {'message': 'Invalid file entry'}
                try:
                    size = int(entry.get('size') or 0)
                except (TypeError, ValueError):
                    return 400, {'message': 'Invalid file size'}
                if size < 0 or (limit and size > limit):
                    return 413, {'message': 'File too large'}
                accepted[str(file_id)] = {
                    'name': str(entry.get('fileName') or file_id),
                    'size': size,
                    'token': secrets.token_urlsafe(16),
                }
            if len(self.sessions) >= max(1, int(config.get('maxConcurrent') or 1)):
                return 409, {'message': 'Blocked by another session'}
            info = request.get('info') if isinstance(request.get('info'), dict) else {}
            session_id = secrets.token_urlsafe(16)
            self.sessions[session_id] = {
                'ip': ip, 'files': accepted, 'touched': now,
                'sender': str(info.get('alias') or ip),
            }
            return 200, {'sessionId': session_id,
                         'files': {key: value['token'] for key, value in accepted.items()}}

    def claim(self, session_id, file_id, token, ip):
        """Check an upload request. Returns (status, entry-or-message)."""
        with self.lock:
            self._expire(self.clock())
            session = self.sessions.get(session_id or '')
            if session is None:
                return 403, {'message': 'Unknown session'}
            if session['ip'] != ip:
                return 403, {'message': 'Wrong address'}
            entry = session['files'].get(file_id or '')
            if entry is None or not hmac.compare_digest(
                    (token or '').encode(), entry['token'].encode()):
                return 403, {'message': 'Invalid token'}
            if entry.get('busy'):
                return 409, {'message': 'Upload already running'}
            entry['busy'] = True
            session['touched'] = self.clock()
            return 200, dict(entry, sender=session['sender'])

    def release(self, session_id, file_id, done):
        """End an upload; a finished file leaves the session for good."""
        with self.lock:
            session = self.sessions.get(session_id)
            if session is None:
                return
            entry = session['files'].get(file_id)
            if entry is not None:
                if done:
                    del session['files'][file_id]
                else:
                    entry['busy'] = False
            session['touched'] = self.clock()
            if not session['files']:
                del self.sessions[session_id]

    def cancel(self, session_id, ip):
        with self.lock:
            session = self.sessions.get(session_id or '')
            if session is not None and session['ip'] == ip:
                del self.sessions[session_id]
                return True
            return False


def answer_announcements(server, sock):
    """Reply to LocalSend multicast announcements while receiving is on."""
    while True:
        try:
            payload, address = sock.recvfrom(65535)
        except OSError:
            return
        try:
            info = json.loads(payload)
        except ValueError:
            continue
        if (not isinstance(info, dict) or not info.get('announce')
                or device_is_self(info, server.fingerprint)):
            continue
        server.book.remember(info, address[0])
        if not server.nextcloud.config().get('enabled'):
            continue
        reply = json.loads(announce_message(server.fingerprint, server.alias,
                                            server.port, server.model))
        reply['announce'] = False
        try:
            sock.sendto(json.dumps(reply).encode(), MULTICAST)
        except OSError:
            pass


def multicast_listener():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    if hasattr(socket, 'SO_REUSEPORT'):
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
    sock.bind(('', MULTICAST[1]))
    membership = socket.inet_aton(MULTICAST[0]) + socket.inet_aton('0.0.0.0')
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, membership)
    return sock


class SendHandler(BaseHTTPRequestHandler):
    server_version = 'localsend-relay/1.0'
    timeout = 120

    def _respond(self, status, payload):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self):
        header = self.headers.get('Authorization') or ''
        return hmac.compare_digest(header, 'Bearer ' + self.server.token)

    def do_GET(self):
        if self.path == '/healthz':
            self._respond(200, {'status': 'ok'})
            return
        if self.path.startswith('/devices'):
            if not self._authorized():
                self._respond(401, {'error': 'unauthorized'})
                return
            devices = discover(self.server.book, self.server.fingerprint,
                               self.server.alias, self.server.port,
                               self.server.discovery_timeout, self.server.model,
                               self.server.scan)
            self._respond(200, {'devices': [public_device(d) for d in devices]})
            return
        if self.path.startswith('/api/localsend/v2/info'):
            self._respond(200, registration_body(self.server.alias, self.server.fingerprint,
                                                 self.server.port, self.server.model))
            return
        self._respond(404, {'error': 'not found'})

    def do_POST(self):
        route = urlsplit(self.path)
        query = {key: values[0] for key, values in parse_qs(route.query).items()}
        if route.path in ('/api/localsend/v2/prepare-upload',
                          '/api/localsend/v2/upload', '/api/localsend/v2/cancel'):
            self._handle_receive(route.path.rsplit('/', 1)[1], query)
            return
        if self.path.startswith('/api/localsend/v2/register'):
            try:
                info = json.loads(read_body(self, self._content_length()) or b'{}')
            except ValueError:
                self._respond(400, {'error': 'invalid json'})
                return
            if isinstance(info, dict) and not device_is_self(
                    info, self.server.fingerprint):
                self.server.book.remember(info, self.client_address[0])
            self._respond(200, {
                'alias': self.server.alias,
                'version': PROTOCOL_VERSION,
                'deviceModel': self.server.model,
                'deviceType': 'server',
                'fingerprint': self.server.fingerprint,
                'download': False,
            })
            return
        if self.path.startswith('/send'):
            if not self._authorized():
                self._respond(401, {'error': 'unauthorized'})
                return
            self._handle_send()
            return
        self._respond(404, {'error': 'not found'})

    def _content_length(self):
        try:
            return int(self.headers.get('Content-Length', '0') or 0)
        except ValueError:
            return 0

    def _handle_send(self):
        target = (self.headers.get('X-Send-To') or '').lower()
        filename = unquote(self.headers.get('X-Send-Filename') or 'file')
        username = unquote(self.headers.get('X-Send-User') or '')
        length = self._content_length()
        if length <= 0 and self.headers.get('Transfer-Encoding', '').lower() != 'chunked':
            self._respond(400, {'error': 'missing body'})
            return
        if length > self.server.max_bytes:
            self._respond(413, {'error': 'file size not allowed'})
            return
        device = self.server.book.find(target)
        if device is None:
            # Not seen yet: look for it once more on demand.
            discover(self.server.book, self.server.fingerprint, self.server.alias,
                     self.server.port, self.server.discovery_timeout, self.server.model,
                     self.server.scan)
            device = self.server.book.find(target)
        if device is None:
            self._respond(404, {'error': 'device not found on the LAN'})
            return
        content = read_body(self, length)
        prefix = f'{username} ' if username else ''
        try:
            result = send_file(device, device['source_ip'], prefix + filename, content,
                               self.server.alias, self.server.fingerprint,
                               self.server.port, self.server.model)
        except Exception as error:  # noqa: BLE001 - report any protocol failure
            self._respond(502, {'error': str(error)})
            return
        self._respond(200, result)

    def _handle_receive(self, action, query):
        sessions = getattr(self.server, 'sessions', None)
        nextcloud = getattr(self.server, 'nextcloud', None)
        ip = self.client_address[0]
        if sessions is None or nextcloud is None:
            self.close_connection = True
            self._respond(403, {'message': 'Receiving is not set up on this relay'})
            return
        if action == 'cancel':
            sessions.cancel(query.get('sessionId'), ip)
            self._respond(200, {})
            return
        if action == 'prepare-upload':
            length = self._content_length()
            if length > 1024 * 1024:
                self.close_connection = True
                self._respond(413, {'message': 'Request too large'})
                return
            try:
                request = json.loads(read_body(self, length) or b'{}')
            except ValueError:
                self._respond(400, {'message': 'Invalid JSON'})
                return
            status, payload = sessions.prepare(nextcloud.config(), request,
                                               query.get('pin'), ip)
            self._respond(status, payload)
            return
        session_id, file_id = query.get('sessionId'), query.get('fileId')
        status, entry = sessions.claim(session_id, file_id, query.get('token'), ip)
        if status != 200:
            self.close_connection = True
            self._respond(status, entry)
            return
        done = False
        try:
            length = self._content_length()
            if 'Content-Length' not in self.headers or length != entry['size']:
                self.close_connection = True
                self._respond(400, {'message': 'Content-Length must match the announced size'})
                return
            status, payload = nextcloud.deliver(entry['name'], entry['sender'], length,
                                                LimitedReader(self.rfile, length))
            if status != 200:
                self.close_connection = True
                self._respond(500, {'message': payload.get('error') or 'Could not store the file'})
                return
            done = True
            self._respond(200, {})
        except Exception as error:  # noqa: BLE001 - report delivery failures
            self.close_connection = True
            self._respond(500, {'message': f'Could not store the file: {error}'})
        finally:
            sessions.release(session_id, file_id, done)

    def log_message(self, fmt, *args):
        print(f'{self.address_string()} {fmt % args}', flush=True)


def env_required(name):
    value = os.environ.get(name, '').strip()
    if not value:
        raise SystemExit(f'{name} is not set')
    return value


def main():
    server = ThreadingHTTPServer(
        ('0.0.0.0', int(os.environ.get('LOCALSEND_SEND_PORT', '53200'))), SendHandler)
    server.token = env_required('LOCALSEND_SEND_TOKEN')
    server.fingerprint = env_required('LOCALSEND_SEND_FINGERPRINT')
    server.port = int(os.environ.get('LOCALSEND_SEND_PORT', '53200'))
    server.alias = os.environ.get('LOCALSEND_SEND_ALIAS', 'nextcloud')
    server.model = os.environ.get('LOCALSEND_SEND_MODEL', 'localsend-send')
    server.discovery_timeout = float(os.environ.get('LOCALSEND_SEND_TIMEOUT', '3'))
    server.scan = resolve_scan_network(os.environ.get('LOCALSEND_SEND_SCAN'))
    server.max_bytes = int(os.environ.get('LOCALSEND_SEND_MAX_BYTES',
                                          str(2 * 1024 * 1024 * 1024)))
    server.book = DeviceBook()
    servers = [server]
    nextcloud_url = os.environ.get('LOCALSEND_RECEIVE_NEXTCLOUD_URL', '').strip()
    if nextcloud_url:
        server.nextcloud = NextcloudLink(
            nextcloud_url, server.token,
            insecure=os.environ.get('LOCALSEND_RECEIVE_INSECURE', '') in ('1', 'true', 'yes'))
        server.sessions = ReceiveSessions()
        # LocalSend apps scan port 53317 over HTTP; listen there as well.
        extra = os.environ.get('LOCALSEND_RECEIVE_PORT', str(DEVICE_PORT)).strip()
        if extra.lower() not in ('', 'none', 'off', '0') and int(extra) != server.port:
            second = ThreadingHTTPServer(('0.0.0.0', int(extra)), SendHandler)
            for name in ('token', 'fingerprint', 'port', 'alias', 'model',
                         'discovery_timeout', 'scan', 'max_bytes', 'book',
                         'nextcloud', 'sessions'):
                setattr(second, name, getattr(server, name))
            servers.append(second)
        try:
            listener = multicast_listener()
            threading.Thread(target=answer_announcements, args=(server, listener),
                             daemon=True).start()
        except OSError as error:
            print(f'receive: multicast listener unavailable: {error}', flush=True)
    for extra_server in servers[1:]:
        threading.Thread(target=extra_server.serve_forever, daemon=True).start()
    server.serve_forever()


if __name__ == '__main__':
    main()
