#!/usr/bin/python3
"""ZeroSend: send and receive files with LocalSend-compatible apps on the CardputerZero.

An APPLaunch app that draws straight to the LCD framebuffer with PIL and reads
the internal keyboard. While it is open it speaks the LocalSend v2 protocol
(https://github.com/localsend/protocol): it announces itself on the LAN by
UDP multicast, answers on HTTPS port 53317, asks on screen before accepting
files (or accepts automatically, if set), and sends files or text to the
devices it finds. Nothing runs once the app is closed.

Files land in ~/Downloads. Settings: ~/.config/zerosend/config.json.
"""
import argparse
import array
import ctypes
import datetime
import fcntl
import hashlib
import http.client
import http.server
import ipaddress
import json
import mimetypes
import mmap
import os
from pathlib import Path
import secrets
import select
import shutil
import signal
import socket
import ssl
import struct
import subprocess
import sys
import threading
import time
import traceback
import urllib.parse
import uuid

from PIL import Image, ImageDraw, ImageFont, ImageMath

APP_NAME = 'ZeroSend'
PKG = 'zerosend'
VERSION = '1.0.0'
PROTOCOL_VERSION = '2.1'
DEVICE_MODEL = 'CardputerZero'
DEVICE_TYPE = 'mobile'
PORT = int(os.environ.get('ZEROSEND_PORT', '53317'))
MULTICAST_GROUP = '224.0.0.167'
MULTICAST_PORT = int(os.environ.get('ZEROSEND_MULTICAST_PORT', '53317'))
API = '/api/localsend/v2/'

HOME = Path.home()
STATE_DIR = Path(os.environ.get('ZEROSEND_STATE', HOME / '.config' / PKG))
CONFIG_FILE = STATE_DIR / 'config.json'
DOWNLOADS = Path(os.environ.get('ZEROSEND_DOWNLOADS', HOME / 'Downloads'))
BACKLIGHT = Path('/sys/class/backlight/backlight/brightness')
KEYBOARD = '/dev/input/cardputer-zero-internal'
LAUNCHER_CONFIG = HOME / '.config/cardputerzero/config.json'

DEJAVU = '/usr/share/fonts/truetype/dejavu/'
FONT, FONT_BOLD = DEJAVU + 'DejaVuSans.ttf', DEJAVU + 'DejaVuSans-Bold.ttf'
FONT_TITLE = '/usr/share/APPLaunch/share/font/Montserrat-Bold.ttf'
FONT_CJK = ['/usr/share/APPLaunch/share/font/NotoSansCJK-Regular.ttc',
            '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc']

# LocalSend's dark theme: Material 3 colour scheme from the seed Colors.teal (#009688).
BG = '#0e1513'        # surface
FG = '#dde4e1'        # onSurface
MUTED = '#bec9c5'     # onSurfaceVariant
FAINT = '#899390'     # outline
DIM = '#5b6563'
ROW = '#334b47'       # secondaryContainer: selected row
TRACK = '#252b2a'     # surfaceContainerHigh: progress track
RULE = '#3f4946'      # outlineVariant
ACCENT = '#82d5c8'    # primary
ON_ACCENT = '#003731' # onPrimary
GREEN = ACCENT        # done / visible: LocalSend shows success in its primary colour
YELLOW = '#ffb74d'    # warnings (LocalSend uses orange)
RED = '#ffb4ab'       # error
VISIBLE = 5


def log(*a):
    print(time.strftime('%H:%M:%S'), *a, flush=True)


def read(path, default=''):
    try:
        return Path(path).read_text().strip()
    except OSError:
        return default


def human_size(n):
    n = float(n)
    for unit in ('B', 'KB', 'MB', 'GB'):
        if n < 1000 or unit == 'GB':
            return f'{n:.0f} {unit}' if unit == 'B' or n >= 100 else f'{n:.1f} {unit}'
        n /= 1000
    return f'{n:.1f} TB'


# ---------------------------------------------------------------- settings

def migrate_old_state():
    """Keep the certificate and settings of the pre-release name (cz-beam)."""
    old = HOME / '.config' / 'cz-beam'
    if not STATE_DIR.exists() and old.is_dir() and 'ZEROSEND_STATE' not in os.environ:
        shutil.copytree(old, STATE_DIR, ignore=shutil.ignore_patterns('app.lock', 'app.log'))


def load_config():
    config = {'alias': socket.gethostname() or 'Cardputer', 'accept': 'ask',
              'manual': [], 'last_dir': str(HOME)}
    try:
        config.update(json.loads(CONFIG_FILE.read_text()))
    except (OSError, ValueError):
        pass
    return config


def save_config(config):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    temporary = CONFIG_FILE.with_suffix('.tmp')
    temporary.write_text(json.dumps(config, indent=2, ensure_ascii=False))
    os.replace(temporary, CONFIG_FILE)


def local_addresses():
    """[(interface, IPv4)] of the interfaces that are up, loopback excluded."""
    try:
        out = subprocess.run(['ip', '-j', '-4', 'addr', 'show', 'up'], capture_output=True,
                             text=True, timeout=5).stdout
        result = []
        for link in json.loads(out or '[]'):
            for info in link.get('addr_info', []):
                if info.get('family') == 'inet' and not info['local'].startswith('127.'):
                    result.append((link['ifname'], info['local']))
        return result
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return []


def lan_addresses():
    """Addresses multicast can work on (no Tailscale/VPN point-to-point links)."""
    return [(name, ip) for name, ip in local_addresses()
            if not name.startswith(('tailscale', 'tun', 'wg', 'docker', 'veth'))]


# ---------------------------------------------------------------- identity

def identity():
    """Self-signed TLS certificate; the LocalSend fingerprint is its SHA-256."""
    cert, key = STATE_DIR / 'cert.pem', STATE_DIR / 'key.pem'
    if not (cert.exists() and key.exists()):
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID
        private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'LocalSend User')])
        now = datetime.datetime.now(datetime.timezone.utc)
        certificate = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
                       .public_key(private.public_key()).serial_number(x509.random_serial_number())
                       .not_valid_before(now - datetime.timedelta(days=1))
                       .not_valid_after(now + datetime.timedelta(days=3650))
                       .sign(private, hashes.SHA256()))
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        key.write_bytes(private.private_bytes(serialization.Encoding.PEM,
                                              serialization.PrivateFormat.TraditionalOpenSSL,
                                              serialization.NoEncryption()))
        key.chmod(0o600)
        cert.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    der = ssl.PEM_cert_to_DER_cert(cert.read_text())
    return str(cert), str(key), hashlib.sha256(der).hexdigest().upper()


# ---------------------------------------------------------------- shared state

class Peer:
    def __init__(self, info, ip):
        self.ip = ip
        self.alias = str(info.get('alias') or ip)[:60]
        self.model = str(info.get('deviceModel') or '')[:40]
        self.type = str(info.get('deviceType') or 'desktop')
        self.fingerprint = str(info.get('fingerprint') or ip)
        self.port = int(info.get('port') or PORT)
        self.protocol = 'http' if info.get('protocol') == 'http' else 'https'
        self.seen = time.monotonic()
        self.manual = False

    def key(self):
        return self.fingerprint


class Transfer:
    """One send or receive session, read by the UI and written by worker threads."""

    def __init__(self, direction, peer_alias, files):
        self.direction = direction          # 'send' | 'receive'
        self.peer = peer_alias
        self.files = files                  # [{'id','name','size',...}]
        self.total = sum(f['size'] for f in files)
        self.done_bytes = 0
        self.current = ''
        self.file_index = 0
        self.state = 'connecting'           # connecting waiting sending done declined busy failed cancelled
        self.error = ''
        self.started = time.monotonic()
        self.last_activity = time.monotonic()
        self.saved = []                     # receive: paths written
        self.cancel = threading.Event()
        self.speed = 0.0
        self._mark = (time.monotonic(), 0)

    def add(self, n):
        self.done_bytes += n
        self.last_activity = now = time.monotonic()
        t0, b0 = self._mark
        if now - t0 >= 1:
            self.speed = (self.done_bytes - b0) / (now - t0)
            self._mark = (now, self.done_bytes)

    def finished(self):
        return self.state in ('done', 'declined', 'busy', 'failed', 'cancelled')


class Incoming:
    """A prepare-upload waiting for the user to accept or decline."""

    def __init__(self, session_id, info, ip, files):
        self.session_id = session_id
        self.info = info
        self.ip = ip
        self.files = files
        self.decision = None
        self.answered = threading.Event()


class Core:
    """Everything the network threads and the UI share."""

    def __init__(self):
        self.lock = threading.RLock()
        self.config = load_config()
        self.cert, self.key, self.fingerprint = identity()
        CLIENT_CERT[:] = [self.cert, self.key]
        self.peers = {}
        self.incoming = None                # Incoming waiting for an answer
        self.receive = None                 # receive session: dict
        self.transfer = None                # Transfer shown in the UI
        self.messages = []                  # received text messages (alias, text)
        self.error = ''
        self.wake_r, self.wake_w = os.pipe()
        os.set_blocking(self.wake_r, False)
        os.set_blocking(self.wake_w, False)

    def wake(self):
        try:
            os.write(self.wake_w, b'.')
        except OSError:
            pass

    def info(self, http_fields=True):
        info = {'alias': self.config['alias'], 'version': PROTOCOL_VERSION,
                'deviceModel': DEVICE_MODEL, 'deviceType': DEVICE_TYPE,
                'fingerprint': self.fingerprint, 'download': False}
        if http_fields:
            info.update(port=PORT, protocol='https')
        return info

    def add_peer(self, info, ip, manual=False):
        if not isinstance(info, dict) or info.get('fingerprint') == self.fingerprint:
            return None
        peer = Peer(info, ip)
        with self.lock:
            old = self.peers.get(peer.key())
            # one entry per address: a device seen again with a new fingerprint replaces the old one
            for k, p in list(self.peers.items()):
                if p.ip == ip and p.port == peer.port and k != peer.key():
                    peer.manual = peer.manual or p.manual
                    del self.peers[k]
            if old:
                peer.manual = old.manual
            peer.manual = peer.manual or manual
            self.peers[peer.key()] = peer
        self.wake()
        return peer

    def peer_list(self):
        with self.lock:
            return sorted(self.peers.values(), key=lambda p: (p.alias.lower(), p.ip))


# ---------------------------------------------------------------- HTTP client

CLIENT_CERT = []   # (cert, key) of this device: LocalSend 1.18+ requires a client certificate


def connect(peer, timeout):
    if peer.protocol == 'https':
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE   # LocalSend certificates are self-signed
        if CLIENT_CERT:
            context.load_cert_chain(*CLIENT_CERT)
        return http.client.HTTPSConnection(peer.ip, peer.port, timeout=timeout, context=context)
    return http.client.HTTPConnection(peer.ip, peer.port, timeout=timeout)


def request_json(peer, path, body=None, query=None, timeout=8, holder=None):
    """POST JSON; returns (status, parsed body or None)."""
    url = API + path + ('?' + urllib.parse.urlencode(query) if query else '')
    conn = connect(peer, timeout)
    if holder is not None:
        holder.append(conn)
    try:
        data = json.dumps(body).encode() if body is not None else b''
        conn.request('POST', url, body=data, headers={'Content-Type': 'application/json',
                                                      'Content-Length': str(len(data))})
        response = conn.getresponse()
        raw = response.read()
        try:
            parsed = json.loads(raw) if raw else None
        except ValueError:
            parsed = None
        return response.status, parsed
    finally:
        conn.close()


def register_with(core, ip, port=PORT, protocol=None, manual=False):
    """POST /register; on success the device is added to the list."""
    for proto in ([protocol] if protocol else ['https', 'http']):
        probe = Peer({'alias': ip, 'port': port, 'protocol': proto}, ip)
        try:
            status, body = request_json(probe, 'register', core.info(), timeout=4)
        except (OSError, http.client.HTTPException, ssl.SSLError):
            continue
        if status == 200 and isinstance(body, dict):
            body = dict(body, port=port, protocol=proto)
            return core.add_peer(body, ip, manual=manual)
    return None


# ---------------------------------------------------------------- discovery

class Discovery(threading.Thread):
    def __init__(self, core):
        super().__init__(daemon=True)
        self.core = core
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if hasattr(socket, 'SO_REUSEPORT'):
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        self.sock.bind(('', MULTICAST_PORT))
        self.sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)
        self.joined = set()
        self.join()

    def join(self):
        for _, ip in lan_addresses():
            if ip in self.joined:
                continue
            mreq = socket.inet_aton(MULTICAST_GROUP) + socket.inet_aton(ip)
            try:
                self.sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
                self.joined.add(ip)
                log('multicast joined on', ip)
            except OSError as error:
                log('multicast join failed on', ip, error)

    def send(self, announce):
        message = dict(self.core.info(), announce=announce, announcement=announce)
        data = json.dumps(message).encode()
        self.join()
        for _, ip in lan_addresses():
            try:
                self.sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(ip))
                self.sock.sendto(data, (MULTICAST_GROUP, MULTICAST_PORT))
            except OSError as error:
                log('announce failed on', ip, error)

    def announce(self):
        def burst():
            for delay in (0, 0.5, 2):   # like LocalSend: a few times in case one is lost
                time.sleep(delay)
                self.send(True)
        threading.Thread(target=burst, daemon=True).start()

    def answer(self, info, ip):
        port = int(info.get('port') or PORT)
        proto = 'http' if info.get('protocol') == 'http' else 'https'
        if not register_with(self.core, ip, port, proto):
            self.send(False)

    def run(self):
        while True:
            try:
                data, (ip, _) = self.sock.recvfrom(65535)
                info = json.loads(data)
            except (OSError, ValueError):
                continue
            if not isinstance(info, dict) or info.get('fingerprint') == self.core.fingerprint:
                continue
            if self.core.add_peer(info, ip) and (info.get('announce') or info.get('announcement')):
                threading.Thread(target=self.answer, args=(info, ip), daemon=True).start()


# ---------------------------------------------------------------- HTTPS server

def safe_relative(name):
    """A received file name as a path inside the download folder (folders kept)."""
    parts = []
    for part in str(name).replace('\\', '/').split('/'):
        part = ''.join(c for c in part if c >= ' ' and c != '\x7f').strip()
        if part and part not in ('.', '..'):
            parts.append(part[:200])
    return Path(*parts) if parts else Path('file')


def unique_path(path):
    if not path.exists():
        return path
    stem, suffix = path.stem, path.suffix
    for i in range(1, 10000):
        candidate = path.with_name(f'{stem} ({i}){suffix}')
        if not candidate.exists():
            return candidate
    return path.with_name(f'{stem} {uuid.uuid4().hex[:6]}{suffix}')


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    server_version = 'ZeroSend'
    core = None

    def log_message(self, fmt, *args):
        log('http', self.client_address[0], fmt % args)

    def reply(self, status, body=None):
        data = json.dumps(body).encode() if body is not None else b''
        self.send_response(status)
        if data:
            self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def body_json(self):
        length = int(self.headers.get('Content-Length') or 0)
        if length > 4 * 1024 * 1024:
            raise ValueError('body too large')
        return json.loads(self.rfile.read(length) or b'null')

    def body_chunks(self):
        if self.headers.get('Transfer-Encoding', '').lower() == 'chunked':
            while True:
                size = int(self.rfile.readline().split(b';')[0].strip() or b'0', 16)
                if size == 0:
                    while self.rfile.readline() not in (b'\r\n', b'\n', b''):
                        pass
                    return
                left = size
                while left:
                    chunk = self.rfile.read(min(left, 65536))
                    if not chunk:
                        raise ConnectionError('connection closed')
                    left -= len(chunk)
                    yield chunk
                self.rfile.readline()
        else:
            left = int(self.headers.get('Content-Length') or 0)
            while left:
                chunk = self.rfile.read(min(left, 65536))
                if not chunk:
                    raise ConnectionError('connection closed')
                left -= len(chunk)
                yield chunk

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path in (API + 'info', '/api/localsend/v1/info'):
            self.reply(200, self.core.info(http_fields=False))
        else:
            self.reply(404)

    def do_POST(self):
        url = urllib.parse.urlparse(self.path)
        query = dict(urllib.parse.parse_qsl(url.query))
        name = url.path[len(API):] if url.path.startswith(API) else ''
        try:
            if name == 'register':
                self.core.add_peer(self.body_json(), self.client_address[0])
                self.reply(200, self.core.info(http_fields=False))
            elif name == 'info':
                self.reply(200, self.core.info(http_fields=False))
            elif name == 'prepare-upload':
                self.prepare_upload(query)
            elif name == 'upload':
                self.upload(query)
            elif name == 'cancel':
                self.cancel(query)
            else:
                self.reply(404)
        except (ValueError, KeyError, TypeError) as error:
            log('bad request', name, error)
            self.reply(400)

    def prepare_upload(self, query):
        core = self.core
        body = self.body_json()
        info, files = body['info'], body['files']
        if not isinstance(files, dict) or not files:
            raise ValueError('no files')
        ip = self.client_address[0]
        core.add_peer(dict(info, port=info.get('port', PORT)), ip)
        alias = str(info.get('alias') or ip)

        # A single text file with a preview is a message: show it, nothing to transfer.
        values = list(files.values())
        if (len(values) == 1 and str(values[0].get('fileType', '')).startswith('text/')
                and values[0].get('preview') is not None):
            with core.lock:
                core.messages.append((alias, str(values[0]['preview'])))
            core.wake()
            wake_screen()
            self.reply(204)
            return

        with core.lock:
            busy = core.incoming is not None or (
                core.receive is not None and not core.receive['transfer'].finished())
            busy = busy or (core.transfer is not None and not core.transfer.finished())
            if busy:
                self.reply(409)
                return
            session_id = uuid.uuid4().hex
            wanted = [{'id': str(k), 'name': str(v.get('fileName') or k), 'size': int(v.get('size') or 0),
                       'sha256': v.get('sha256')} for k, v in files.items()]
            incoming = Incoming(session_id, info, ip, wanted)
            if core.config.get('accept') == 'auto':
                incoming.decision = True
                incoming.answered.set()
            else:
                core.incoming = incoming
        core.wake()
        wake_screen()
        incoming.answered.wait(600)
        with core.lock:
            if core.incoming is incoming:
                core.incoming = None
            if not incoming.decision:
                self.reply(403)
                core.wake()
                return
            tokens = {f['id']: secrets.token_hex(16) for f in wanted}
            transfer = Transfer('receive', alias, wanted)
            transfer.state = 'sending'
            core.receive = {'id': session_id, 'ip': ip, 'tokens': tokens, 'transfer': transfer,
                            'files': {f['id']: f for f in wanted}, 'left': set(tokens)}
            core.transfer = transfer
        core.wake()
        self.reply(200, {'sessionId': session_id, 'files': tokens})

    def upload(self, query):
        core = self.core
        with core.lock:
            session = core.receive
            valid = (session is not None and query.get('sessionId') == session['id']
                     and session['tokens'].get(query.get('fileId')) == query.get('token')
                     and self.client_address[0] == session['ip'])
            if not valid or session['transfer'].finished():
                self.reply(403 if not valid else 409)
                return
            meta = session['files'][query['fileId']]
            transfer = session['transfer']
        relative = safe_relative(meta['name'])
        target = DOWNLOADS / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        part = target.with_name('.' + target.name + '.part')
        digest = hashlib.sha256() if meta.get('sha256') else None
        transfer.current = relative.name
        transfer.file_index = len(transfer.files) - len(session['left']) + 1
        core.wake()
        received = 0
        try:
            with open(part, 'wb') as out:
                for chunk in self.body_chunks():
                    if transfer.cancel.is_set():
                        raise ConnectionAbortedError('cancelled')
                    out.write(chunk)
                    received += len(chunk)
                    if digest:
                        digest.update(chunk)
                    with core.lock:
                        transfer.add(len(chunk))
        except (OSError, ConnectionError) as error:
            part.unlink(missing_ok=True)
            with core.lock:
                if not transfer.finished():
                    transfer.state = 'cancelled' if transfer.cancel.is_set() else 'failed'
                    transfer.error = str(error)[:120]
            core.wake()
            log('upload failed', relative, error)
            try:
                self.reply(500)
            except OSError:
                pass
            return
        if digest and digest.hexdigest().lower() != str(meta['sha256']).lower():
            part.unlink(missing_ok=True)
            self.reply(422)
            return
        with core.lock:
            final = unique_path(target)
            os.replace(part, final)
            transfer.saved.append(final)
            session['left'].discard(meta['id'])
            if not session['left'] and not transfer.finished():
                transfer.state = 'done'
        log('received', final, received, 'bytes')
        core.wake()
        self.reply(200)

    def cancel(self, query):
        core = self.core
        with core.lock:
            session = core.receive
            if session and query.get('sessionId') == session['id'] and not session['transfer'].finished():
                session['transfer'].cancel.set()
                session['transfer'].state = 'cancelled'
                session['transfer'].error = 'Cancelled by sender'
            if core.incoming and core.incoming.ip == self.client_address[0]:
                core.incoming.decision = False
                core.incoming.answered.set()
                core.incoming = None
        core.wake()
        self.reply(200)


class Server(http.server.ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, core):
        Handler.core = core
        super().__init__(('', PORT), Handler)
        self.context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.context.load_cert_chain(core.cert, core.key)

    def finish_request(self, request, client_address):
        # TLS handshake in the worker thread, so a slow client cannot block accept().
        request.settimeout(30)
        try:
            request = self.context.wrap_socket(request, server_side=True)
        except (OSError, ssl.SSLError) as error:
            log('tls failed', client_address[0], error)
            return
        request.settimeout(120)
        self.RequestHandlerClass(request, client_address, self)


# ---------------------------------------------------------------- sending

def collect(paths):
    """Files to send: [{'path', 'name'}]; folders are sent with their contents."""
    out = []
    for path in paths:
        path = Path(path)
        if path.is_dir():
            for root, dirs, names in os.walk(path):
                dirs.sort()
                for name in sorted(names):
                    full = Path(root) / name
                    if full.is_file():
                        out.append({'path': full, 'name': str(full.relative_to(path.parent)).replace(os.sep, '/')})
        elif path.is_file():
            out.append({'path': path, 'name': path.name})
    return out


def send_job(core, peer, paths=None, text=None):
    if text is not None:
        data = text.encode()
        files = [{'id': uuid.uuid4().hex, 'name': uuid.uuid4().hex + '.txt', 'size': len(data),
                  'type': 'text/plain', 'preview': text, 'data': data}]
    else:
        files = []
        for item in collect(paths):
            stat = item['path'].stat()
            files.append({'id': uuid.uuid4().hex, 'name': item['name'], 'size': stat.st_size,
                          'path': item['path'], 'modified': stat.st_mtime,
                          'type': mimetypes.guess_type(item['name'])[0] or 'application/octet-stream'})
    transfer = Transfer('send', peer.alias, files)
    transfer.text = text
    with core.lock:
        core.transfer = transfer
    core.wake()

    def finish(state, error=''):
        with core.lock:
            if not transfer.finished():
                transfer.state = state
                transfer.error = error
        core.wake()

    if not files:
        finish('failed', 'Nothing to send (empty folder?)')
        return
    body = {'info': core.info(), 'files': {}}
    for f in files:
        entry = {'id': f['id'], 'fileName': f['name'], 'size': f['size'], 'fileType': f['type'],
                 'sha256': None, 'preview': f.get('preview')}
        if 'modified' in f:
            stamp = datetime.datetime.fromtimestamp(f['modified'], datetime.timezone.utc)
            entry['metadata'] = {'modified': stamp.strftime('%Y-%m-%dT%H:%M:%SZ'), 'accessed': None}
        body['files'][f['id']] = entry
    transfer.state = 'waiting'
    core.wake()
    holder = []
    transfer.holder = holder
    try:
        status, answer = request_json(peer, 'prepare-upload', body, timeout=600, holder=holder)
    except (OSError, http.client.HTTPException, ssl.SSLError) as error:
        finish('cancelled' if transfer.cancel.is_set() else 'failed', short_error(error))
        return
    if transfer.cancel.is_set():
        finish('cancelled')
        return
    if status == 204:
        finish('done')
        return
    if status in (403, 401):
        finish('declined', 'PIN required (not supported)' if status == 401 else '')
        return
    if status == 409:
        finish('busy', 'The device is busy with another transfer')
        return
    if status != 200 or not isinstance(answer, dict):
        finish('failed', f'Refused (HTTP {status})')
        return
    session_id, tokens = answer.get('sessionId'), answer.get('files') or {}
    transfer.state = 'sending'
    core.wake()
    accepted = [f for f in files if f['id'] in tokens]
    with core.lock:
        transfer.total = sum(f['size'] for f in accepted)
    try:
        for index, f in enumerate(accepted, 1):
            transfer.current = Path(f['name']).name
            transfer.file_index = index
            core.wake()
            upload_file(core, peer, transfer, session_id, f, tokens[f['id']], holder)
    except Exception as error:   # noqa: BLE001 - any failure ends the session the same way
        if transfer.cancel.is_set():
            try:
                request_json(peer, 'cancel', query={'sessionId': session_id}, timeout=4)
            except (OSError, http.client.HTTPException, ssl.SSLError):
                pass
            finish('cancelled')
        else:
            log('send failed', traceback.format_exc())
            finish('failed', short_error(error))
        return
    finish('done')


def short_error(error):
    if isinstance(error, (socket.timeout, TimeoutError)):
        return 'No answer from the device'
    if isinstance(error, ConnectionRefusedError):
        return 'Connection refused: is LocalSend open?'
    if isinstance(error, OSError) and error.errno in (113, 101):
        return 'Device unreachable'
    return (str(error) or type(error).__name__)[:80]


def upload_file(core, peer, transfer, session_id, f, token, holder):
    query = urllib.parse.urlencode({'sessionId': session_id, 'fileId': f['id'], 'token': token})
    conn = connect(peer, 60)
    holder.append(conn)
    try:
        conn.putrequest('POST', API + 'upload?' + query)
        conn.putheader('Content-Type', 'application/octet-stream')
        conn.putheader('Content-Length', str(f['size']))
        conn.endheaders()
        if 'data' in f:
            conn.send(f['data'])
            transfer.add(len(f['data']))
        else:
            with open(f['path'], 'rb') as source:
                while True:
                    if transfer.cancel.is_set():
                        raise ConnectionAbortedError('cancelled')
                    chunk = source.read(65536)
                    if not chunk:
                        break
                    conn.send(chunk)
                    with core.lock:
                        transfer.add(len(chunk))
                    core.wake()
        response = conn.getresponse()
        response.read()
        if response.status != 200:
            raise RuntimeError(f'{Path(f["name"]).name}: HTTP {response.status}')
    finally:
        conn.close()


def abort_send(transfer):
    transfer.cancel.set()
    for conn in list(getattr(transfer, 'holder', [])):
        try:
            if conn.sock:
                conn.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass


# ---------------------------------------------------------------- screen + keys

def wake_screen():
    """Turn the LCD on for an incoming request: a Shift tap, like a real key press."""
    if read(BACKLIGHT, '1') not in ('0', ''):
        return
    def tap():
        try:
            fd = os.open(KEYBOARD, os.O_WRONLY)
        except OSError:
            return
        try:
            for value in (1, 0):
                now = time.time()
                sec, usec = int(now), int((now % 1) * 1e6)
                os.write(fd, struct.pack('llHHi', sec, usec, 1, 42, value)
                         + struct.pack('llHHi', sec, usec, 0, 0, 0))
                time.sleep(0.3)
        finally:
            os.close(fd)
    threading.Thread(target=tap, daemon=True).start()


class Bitfield(ctypes.Structure):
    _fields_ = [('offset', ctypes.c_uint32), ('length', ctypes.c_uint32), ('msb_right', ctypes.c_uint32)]


class VarInfo(ctypes.Structure):
    _fields_ = ([(n, ctypes.c_uint32) for n in ('xres', 'yres', 'xres_virtual', 'yres_virtual', 'xoffset', 'yoffset', 'bits_per_pixel', 'grayscale')]
                + [(n, Bitfield) for n in ('red', 'green', 'blue', 'transp')]
                + [(n, ctypes.c_uint32) for n in ('nonstd', 'activate', 'height', 'width', 'accel_flags', 'pixclock', 'left_margin', 'right_margin', 'upper_margin', 'lower_margin', 'hsync_len', 'vsync_len', 'sync', 'vmode', 'rotate', 'colorspace')]
                + [('reserved', ctypes.c_uint32 * 4)])


class FixInfo(ctypes.Structure):
    _fields_ = [('id', ctypes.c_char * 16), ('smem_start', ctypes.c_ulong), ('smem_len', ctypes.c_uint32), ('type', ctypes.c_uint32), ('type_aux', ctypes.c_uint32), ('visual', ctypes.c_uint32), ('xpanstep', ctypes.c_uint16), ('ypanstep', ctypes.c_uint16), ('ywrapstep', ctypes.c_uint16), ('line_length', ctypes.c_uint32), ('mmio_start', ctypes.c_ulong), ('mmio_len', ctypes.c_uint32), ('accel', ctypes.c_uint32), ('capabilities', ctypes.c_uint16), ('reserved', ctypes.c_uint16 * 2)]


class Framebuffer:
    def __init__(self):
        path = '/dev/fb_lcd' if os.path.exists('/dev/fb_lcd') else '/dev/fb0'
        self.fd = os.open(path, os.O_RDWR)
        self.var, self.fix = VarInfo(), FixInfo()
        fcntl.ioctl(self.fd, 0x4600, self.var)  # FBIOGET_VSCREENINFO
        fcntl.ioctl(self.fd, 0x4602, self.fix)  # FBIOGET_FSCREENINFO
        v = self.var
        if (v.xres, v.yres, v.bits_per_pixel, v.red.offset, v.green.offset, v.blue.offset) != (320, 170, 16, 11, 5, 0):
            os.close(self.fd)
            raise RuntimeError('Unsupported LCD framebuffer format')
        self.memory = mmap.mmap(self.fd, self.fix.smem_len, mmap.MAP_SHARED, mmap.PROT_READ | mmap.PROT_WRITE)
        self.previous = None

    def draw(self, image):
        data = image.tobytes()
        if data == self.previous:
            return
        self.previous = data
        try:   # RGB565 in C (10x faster than the Python loop on the CM0)
            r, g, b = image.split()
            raw = ImageMath.lambda_eval(lambda a: ((a['r'] >> 3) << 11) | ((a['g'] >> 2) << 5) | (a['b'] >> 3),
                                        r=r, g=g, b=b).convert('I;16').tobytes()
        except (ValueError, OSError, AttributeError):
            raw = array.array('H', (((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)
                                    for r, g, b in image.getdata())).tobytes()
        for y in range(170):
            start = (y + self.var.yoffset) * self.fix.line_length + self.var.xoffset * 2
            self.memory[start:start + 640] = raw[y * 640:(y + 1) * 640]

    def close(self):
        self.memory.close()
        os.close(self.fd)


# Keyboard: evdev codes of the CardputerZero's three layers (linux,keymap*).
LETTERS = {16: 'q', 17: 'w', 18: 'e', 19: 'r', 20: 't', 21: 'y', 22: 'u', 23: 'i', 24: 'o', 25: 'p',
           30: 'a', 31: 's', 32: 'd', 33: 'f', 34: 'g', 35: 'h', 36: 'j', 37: 'k', 38: 'l',
           44: 'z', 45: 'x', 46: 'c', 47: 'v', 48: 'b', 49: 'n', 50: 'm'}
DIGITS = {2: '1', 3: '2', 4: '3', 5: '4', 6: '5', 7: '6', 8: '7', 9: '8', 10: '9', 11: '0'}
SHIFT_DIGITS = dict(zip('1234567890', '!@#$%^&*()'))
SYM = {26: '!', 27: '@', 39: '#', 40: '$', 41: '%', 43: '^', 51: '&', 52: '*', 53: '(', 94: ')',
       55: '~', 69: '`', 70: '_', 71: '-', 72: '+', 73: '=', 74: '[', 75: ']', 76: '{', 77: '}',
       79: ';', 80: ':', 81: "'", 82: '"', 83: '<', 85: '>', 86: '\\', 89: '|', 90: ',', 91: '.',
       92: '/', 93: '?'}
K_ESC, K_TAB, K_BACKSPACE, K_ENTER, K_SPACE, K_SHIFT = 1, 15, 14, 28, 57, 42
K_UP, K_DOWN, K_LEFT, K_RIGHT, K_DELETE = 103, 108, 105, 106, 111
NAV = {33: K_UP, 45: K_DOWN, 44: K_LEFT, 46: K_RIGHT}   # APPLaunch: F/X/Z/C act as arrows


# ---------------------------------------------------------------- UI

class App:
    def __init__(self, core, discovery=None):
        self.core = core
        self.discovery = discovery
        self.running = True
        self.screen = 'home'
        self.selected = 0
        self.top = 0
        self.message = ''
        self.message_colour = MUTED
        self.message_until = 0
        self.target = None                 # Peer being sent to
        self.menu = 0
        self.browser = None
        self.input = None                  # {'title','text','purpose','hint'}
        self.shift = False
        self.shift_alone = False
        self.shift_sticky = False
        self.showing_message = None        # (alias, text, scroll)
        self.settings_row = 0
        self.addresses = []
        self.addresses_at = 0
        self.fonts = {}

    # ---- fonts
    def font(self, kind, size):
        key = (kind, size)
        if key not in self.fonts:
            path = {'regular': FONT, 'bold': FONT_BOLD, 'title': FONT_TITLE}.get(kind)
            if kind == 'cjk':
                for candidate in FONT_CJK:
                    if os.path.exists(candidate):
                        path = candidate
                        break
                else:
                    path = FONT
            try:
                self.fonts[key] = ImageFont.truetype(path, size)
            except OSError:
                self.fonts[key] = ImageFont.truetype(FONT, size)
        return self.fonts[key]

    def text(self, d, xy, s, size=14, fill=FG, bold=False, max_width=None, anchor_right=False):
        """Draw text; non-Latin text uses the CJK font, long text is shortened with …"""
        cjk = any(ord(c) > 0x2FF and not 0x2000 <= ord(c) <= 0x2BFF for c in s)
        font = self.font('cjk', size) if cjk else self.font('bold' if bold else 'regular', size)
        if max_width is not None and d.textlength(s, font=font) > max_width:
            while s and d.textlength(s + '…', font=font) > max_width:
                s = s[:-1]
            s += '…'
        x, y = xy
        if anchor_right:
            x -= d.textlength(s, font=font)
        d.text((x, y - (2 if cjk else 0)), s, font=font, fill=fill)
        return d.textlength(s, font=font)

    def toast(self, text, colour=MUTED, seconds=4):
        self.message, self.message_colour = text, colour
        self.message_until = time.monotonic() + seconds

    def my_ip(self):
        if time.monotonic() - self.addresses_at > 5:
            self.addresses = lan_addresses()
            self.addresses_at = time.monotonic()
        return self.addresses[0][1] if self.addresses else 'no Wi-Fi'

    # ---- drawing helpers
    def header(self, d, title, sub, status=None):
        d.text((10, 4), title, font=self.font('title', 17), fill=FG)
        if status:
            label, colour = status
            w = d.textlength(label, font=self.font('regular', 11))
            d.ellipse((300 - w - 10, 11, 300 - w - 4, 17), fill=colour)
            d.text((310 - w, 7), label, font=self.font('regular', 11), fill=colour)
        self.text(d, (12, 26), sub, size=11, fill=MUTED, max_width=296)

    def footer(self, d, keys):
        d.line((8, 146, 312, 146), fill=RULE)
        if self.message and time.monotonic() < self.message_until:
            self.text(d, (10, 152), self.message, size=11, fill=self.message_colour, max_width=300)
            return
        x, small = 10, self.font('regular', 11)
        for key, action in keys:
            width = d.textlength(key + ' ' + action, font=small)
            if x + width > 312:
                break
            d.text((x, 152), key, font=small, fill=FG)
            x += d.textlength(key + ' ', font=small)
            d.text((x, 152), action, font=small, fill=FAINT)
            x += d.textlength(action, font=small) + 12

    def row(self, d, slot, selected, left, right='', left_colour=FG, right_colour=FAINT, marker=None):
        y = 43 + 20 * slot
        if selected:
            d.rounded_rectangle((6, y - 1, 305, y + 18), radius=4, fill=ROW)
            d.rounded_rectangle((6, y + 2, 8, y + 15), radius=1, fill=ACCENT)
        x = 14
        if marker:
            marker(d, x, y)
            x += 18
        right_w = 0
        if right:
            right_w = self.text(d, (298, y + 2), right, size=11, fill=right_colour, anchor_right=True,
                                max_width=170)
        self.text(d, (x, y), left, size=14, fill=left_colour, max_width=298 - x - right_w - 8)

    def scrollbar(self, d, count, top):
        if count > VISIBLE:
            h = 100 * VISIBLE // count
            y0 = 42 + (100 - h) * top // (count - VISIBLE)
            d.rounded_rectangle((311, 42, 313, 142), radius=1, fill=RULE)
            d.rounded_rectangle((311, y0, 313, y0 + h), radius=1, fill=FAINT)

    @staticmethod
    def device_icon(kind, colour):
        def draw(d, x, y):
            if kind == 'mobile':
                d.rounded_rectangle((x + 3, y + 2, x + 11, y + 16), radius=2, outline=colour, width=2)
            elif kind == 'web':
                d.ellipse((x + 1, y + 2, x + 15, y + 16), outline=colour, width=2)
                d.line((x + 1, y + 9, x + 15, y + 9), fill=colour)
            elif kind in ('server', 'headless'):
                d.rectangle((x + 1, y + 3, x + 14, y + 8), outline=colour, width=2)
                d.rectangle((x + 1, y + 10, x + 14, y + 15), outline=colour, width=2)
            else:
                d.rectangle((x + 1, y + 3, x + 15, y + 12), outline=colour, width=2)
                d.line((x + 5, y + 15, x + 11, y + 15), fill=colour, width=2)
        return draw

    # ---- screens
    def render(self):
        im = Image.new('RGB', (320, 170), BG)
        d = ImageDraw.Draw(im)
        getattr(self, 'draw_' + self.screen)(d)
        return im

    def status(self):
        if self.core.error:
            return 'Error', RED
        if not self.addresses:
            return 'No Wi-Fi', YELLOW
        return 'Visible', GREEN

    def draw_home(self, d):
        self.my_ip()
        self.header(d, APP_NAME, f'{self.core.config["alias"]}  ·  {self.my_ip()}', self.status())
        peers = self.core.peer_list()
        self.selected = min(self.selected, max(0, len(peers) - 1))
        self.top = max(min(self.top, self.selected), self.selected - VISIBLE + 1, 0)
        if not peers:
            lines = [('Looking for nearby devices…', FG),
                     ('Open LocalSend on a phone or computer', MUTED),
                     ('on the same Wi-Fi. This Cardputer shows', MUTED),
                     (f'up there as “{self.core.config["alias"]}”.', MUTED),
                     ('A adds a device by IP (e.g. Tailscale).', FAINT)]
            if self.core.error:
                lines = [(self.core.error, RED)] + lines[1:]
            for i, (line, colour) in enumerate(lines):
                self.text(d, (12, 44 + i * 19 - (0 if i == 0 else 2)), line, size=14 if i == 0 else 12,
                          fill=colour, max_width=296)
        for slot, i in enumerate(range(self.top, min(self.top + VISIBLE, len(peers)))):
            p = peers[i]
            right = p.ip if not p.model else f'{p.model}'
            self.row(d, slot, i == self.selected, p.alias, right,
                     marker=self.device_icon(p.type, ACCENT if i == self.selected else MUTED))
        self.scrollbar(d, len(peers), self.top)
        self.footer(d, [('Enter', 'Send'), ('R', 'Rescan'), ('A', 'Add IP'), ('S', 'Setup'), ('ESC', 'Exit')])

    def draw_menu(self, d):
        p = self.target
        self.header(d, 'Send to', f'{p.alias}  ·  {p.ip}' + (f'  ·  {p.model}' if p.model else ''))
        for i, (label, _) in enumerate(self.menu_items()):
            self.row(d, i, i == self.menu, label)
        self.footer(d, [('Enter', 'Choose'), ('ESC', 'Back')])

    def menu_items(self):
        items = [('Files and folders', 'files'), ('Text message', 'text')]
        if self.target and self.target.manual:
            items.append(('Forget this device', 'forget'))
        return items

    def draw_browser(self, d):
        b = self.browser
        marked = b['marked']
        if marked:
            size = sum(p.stat().st_size for p in marked if p.is_file())
            sub = f'{len(marked)} selected  ·  {human_size(size)}' + ('+' if any(p.is_dir() for p in marked) else '')
        else:
            sub = str(b['dir']).replace(str(HOME), '~', 1)
            while len(sub) > 46:
                sub = '…' + sub[len(sub) - 45:]
        self.header(d, 'Send to ' + self.target.alias if len(self.target.alias) < 14 else 'Send files', sub)
        entries = b['entries']
        if not entries:
            self.text(d, (14, 46), 'Empty folder' if not b.get('error') else b['error'], size=13, fill=MUTED)
        sel = b['selected']
        b['top'] = max(min(b['top'], sel), sel - VISIBLE + 1, 0)
        for slot, i in enumerate(range(b['top'], min(b['top'] + VISIBLE, len(entries)))):
            path, is_dir, size = entries[i]
            def marker(d, x, y, path=path, is_dir=is_dir):
                if path in marked:
                    d.rounded_rectangle((x + 1, y + 3, x + 13, y + 15), radius=3, fill=GREEN)
                    d.line((x + 4, y + 9, x + 6, y + 12, x + 11, y + 5), fill=ON_ACCENT, width=2)
                elif is_dir:
                    d.polygon((x + 1, y + 4, x + 6, y + 4, x + 8, y + 6, x + 14, y + 6, x + 14, y + 15,
                               x + 1, y + 15), outline=ACCENT if i == sel else MUTED)
                else:
                    d.rectangle((x + 3, y + 3, x + 12, y + 15), outline=FAINT)
            name = path.name + ('/' if is_dir else '') if path.name else '..'
            self.row(d, slot, i == sel, name, '' if is_dir else human_size(size),
                     left_colour=FG, marker=marker)
        self.scrollbar(d, len(entries), b['top'])
        keys = [('Space', 'Mark'), ('Enter', 'Open/Send')]
        if marked:
            keys.append(('Y', f'Send {len(marked)}'))
        keys += [('⌫', 'Up'), ('ESC', 'Back')]
        self.footer(d, keys)

    def draw_input(self, d):
        box = self.input
        self.header(d, box['title'], box.get('hint', ''))
        d.rounded_rectangle((8, 42, 312, 140), radius=5, outline=RULE)
        text = box['text']
        lines, line = [], ''
        font_size = 14
        cjk = any(ord(c) > 0x2FF for c in text)
        font = self.font('cjk' if cjk else 'regular', font_size)
        for paragraph in text.split('\n'):
            line = ''
            for ch in paragraph:
                if d.textlength(line + ch, font=font) > 288:
                    lines.append(line)
                    line = ''
                line += ch
            lines.append(line)
        lines = lines[-5:]
        cursor_on = int(time.monotonic() * 2) % 2 == 0
        for i, line in enumerate(lines):
            y = 47 + i * 18
            d.text((14, y - (2 if cjk else 0)), line, font=font, fill=FG)
            if i == len(lines) - 1 and cursor_on:
                x = 15 + d.textlength(line, font=font)
                d.line((x, y + 1, x, y + 16), fill=ACCENT, width=2)
        if self.shift_sticky or self.shift:
            self.text(d, (300, 124), '⇧', size=12, fill=ACCENT, anchor_right=True)
        self.footer(d, [('Enter', box.get('ok', 'OK')), ('⌫', 'Delete'), ('Sym', 'Symbols'), ('ESC', 'Cancel')])

    def draw_incoming(self, d):
        inc = self.core.incoming
        if inc is None:
            self.screen = 'home'
            return self.draw_home(d)
        alias = str(inc.info.get('alias') or inc.ip)
        model = str(inc.info.get('deviceModel') or '')
        total = sum(f['size'] for f in inc.files)
        self.header(d, 'Incoming', f'{alias}' + (f'  ·  {model}' if model else '') + f'  ·  {inc.ip}',
                    ('Waiting', YELLOW))
        count = len(inc.files)
        self.text(d, (12, 44), f'{count} file{"s" if count != 1 else ""}  ·  {human_size(total)}', size=15,
                  fill=FG, bold=True)
        for i, f in enumerate(inc.files[:3]):
            self.text(d, (14, 66 + i * 17), safe_relative(f['name']).name, size=12, fill=MUTED, max_width=220)
            self.text(d, (298, 67 + i * 17), human_size(f['size']), size=11, fill=FAINT, anchor_right=True)
        if count > 3:
            self.text(d, (14, 117), f'+ {count - 3} more', size=12, fill=FAINT)
        free = shutil.disk_usage(DOWNLOADS if DOWNLOADS.exists() else HOME).free
        if total > free:
            self.text(d, (120, 117), f'Only {human_size(free)} free', size=12, fill=RED)
        self.footer(d, [('Y', 'Accept'), ('N', 'Decline')])

    def draw_transfer(self, d):
        t = self.core.transfer
        if t is None:
            self.screen = 'home'
            return self.draw_home(d)
        sending = t.direction == 'send'
        title = 'Sending' if sending else 'Receiving'
        colours = {'done': GREEN, 'declined': RED, 'busy': YELLOW, 'failed': RED, 'cancelled': YELLOW}
        labels = {'connecting': 'Connecting', 'waiting': 'Waiting', 'sending': 'Transferring', 'done': 'Done',
                  'declined': 'Declined', 'busy': 'Busy', 'failed': 'Failed', 'cancelled': 'Cancelled'}
        self.header(d, title, ('to ' if sending else 'from ') + t.peer,
                    (labels.get(t.state, t.state), colours.get(t.state, ACCENT)))
        count = len(t.files)
        if getattr(t, 'text', None) is not None:
            headline = 'Text message'
        else:
            headline = f'{count} file{"s" if count != 1 else ""}  ·  {human_size(t.total)}'
        self.text(d, (12, 44), headline, size=15, fill=FG, bold=True)
        if t.state == 'waiting':
            self.text(d, (12, 68), f'Waiting for {t.peer} to accept…', size=12, fill=MUTED, max_width=296)
        elif t.state == 'connecting':
            self.text(d, (12, 68), 'Connecting…', size=12, fill=MUTED)
        elif t.state == 'sending':
            self.text(d, (12, 66), f'{t.file_index}/{count}  {t.current}', size=12, fill=MUTED, max_width=296)
        if t.state in ('sending', 'done') and t.total:
            fraction = min(1.0, t.done_bytes / t.total)
            d.rounded_rectangle((12, 88, 308, 98), radius=5, fill=TRACK)
            if fraction > 0:
                d.rounded_rectangle((12, 88, 12 + max(10, int(296 * fraction)), 98), radius=5,
                                    fill=GREEN if t.state == 'done' else ACCENT)
            info = f'{human_size(t.done_bytes)} / {human_size(t.total)}'
            if t.state == 'sending' and t.speed:
                info += f'  ·  {human_size(t.speed)}/s'
            self.text(d, (12, 104), info, size=12, fill=MUTED)
        if t.state == 'done':
            if sending:
                text = 'Delivered' if getattr(t, 'text', None) is not None else 'All files sent'
            else:
                text = 'Saved to ' + str(DOWNLOADS).replace(str(HOME), '~', 1)
            self.text(d, (12, 122), text, size=12, fill=GREEN, max_width=296)
        elif t.finished():
            text = t.error or {'declined': f'{t.peer} declined', 'cancelled': 'Cancelled'}.get(t.state, t.state)
            self.text(d, (12, 122), text, size=12, fill=colours.get(t.state, RED), max_width=296)
        if t.finished():
            self.footer(d, [('Enter', 'OK')])
        else:
            self.footer(d, [('ESC', 'Cancel')])

    def draw_message(self, d):
        alias, text, scroll = self.showing_message
        self.header(d, 'Message', 'from ' + alias)
        font = self.font('cjk', 13)
        lines = []
        for paragraph in text.split('\n'):
            line = ''
            for ch in paragraph:
                if d.textlength(line + ch, font=font) > 292:
                    lines.append(line)
                    line = ''
                line += ch
            lines.append(line)
        self.message_lines = len(lines)
        for i, line in enumerate(lines[scroll:scroll + 6]):
            d.text((12, 41 + i * 17), line, font=font, fill=FG)
        keys = [('Enter', 'Close'), ('S', 'Save as file')]
        if len(lines) > 6:
            keys.insert(1, ('F/X', 'Scroll'))
        self.footer(d, keys)

    def settings_rows(self):
        c = self.core.config
        return [('Name', c['alias'], True),
                ('Incoming files', 'Ask first' if c.get('accept') != 'auto' else 'Accept all', True),
                ('Save to', str(DOWNLOADS).replace(str(HOME), '~', 1), False),
                ('Address', f'{self.my_ip()}:{PORT} https', False),
                ('Fingerprint', self.core.fingerprint[:8] + '…', False)]

    def draw_settings(self, d):
        self.header(d, 'Setup', f'{APP_NAME} {VERSION}  ·  works with LocalSend 2.x')
        for i, (label, value, editable) in enumerate(self.settings_rows()):
            self.row(d, i, i == self.settings_row, label, value, right_colour=FG if editable else FAINT)
        self.footer(d, [('F/X', 'Move'), ('Enter', 'Change'), ('ESC', 'Back')])

    # ---- actions
    def open_browser(self, folder):
        folder = Path(folder)
        entries, error = [], ''
        try:
            items = [p for p in folder.iterdir() if not p.name.startswith('.')]
            dirs = sorted((p for p in items if p.is_dir()), key=lambda p: p.name.lower())
            files = sorted((p for p in items if p.is_file()), key=lambda p: p.name.lower())
            entries = [(p, True, 0) for p in dirs] + [(p, False, p.stat().st_size) for p in files]
        except OSError as e:
            error = e.strerror or str(e)
        old = self.browser or {}
        self.browser = {'dir': folder, 'entries': entries, 'selected': 0, 'top': 0,
                        'marked': old.get('marked', []), 'error': error}
        self.core.config['last_dir'] = str(folder)

    def start_send(self, paths=None, text=None):
        peer = self.target
        self.screen = 'transfer'
        threading.Thread(target=send_job, args=(self.core, peer), kwargs={'paths': paths, 'text': text},
                         daemon=True).start()
        try:
            save_config(self.core.config)
        except OSError:
            pass

    def ask(self, title, purpose, text='', hint='', ok='OK'):
        self.input = {'title': title, 'purpose': purpose, 'text': text, 'hint': hint, 'ok': ok}
        self.shift_sticky = False
        self.screen = 'input'

    def submit_input(self):
        box = self.input
        value = box['text'].strip()
        purpose = box['purpose']
        if purpose == 'text':
            if value:
                self.start_send(text=box['text'])
            return
        if purpose == 'alias':
            if value:
                self.core.config['alias'] = value[:40]
                save_config(self.core.config)
                if self.discovery:
                    self.discovery.announce()
            self.screen = 'settings'
            return
        if purpose == 'add':
            host = value
            port = PORT
            if host.count(':') == 1:
                host, _, port_text = host.partition(':')
                port = int(port_text) if port_text.isdigit() else PORT
            try:
                ipaddress.IPv4Address(host)
            except ValueError:
                try:
                    host = socket.gethostbyname(host)
                except OSError:
                    self.toast('Not an IPv4 address or known name', RED)
                    return
            self.screen = 'home'
            self.toast(f'Looking for LocalSend on {host}…', MUTED, 8)

            def probe():
                peer = register_with(self.core, host, port, manual=True)
                if peer:
                    manual = self.core.config.setdefault('manual', [])
                    if not any(m['ip'] == host and m.get('port', PORT) == port for m in manual):
                        manual.append({'ip': host, 'port': port})
                        save_config(self.core.config)
                    self.toast(f'Added {peer.alias}', GREEN)
                else:
                    self.toast(f'No LocalSend answer from {host}:{port}', RED, 6)
                self.core.wake()
            threading.Thread(target=probe, daemon=True).start()

    def rescan(self):
        with self.core.lock:
            self.core.peers = {k: p for k, p in self.core.peers.items() if p.manual}
        if self.discovery:
            self.discovery.announce()
        probe_manual(self.core)
        self.toast('Looking for devices…', MUTED, 3)

    def type_char(self, code):
        shift = self.shift or self.shift_sticky
        ch = None
        if code in LETTERS:
            ch = LETTERS[code].upper() if shift else LETTERS[code]
        elif code in DIGITS:
            ch = SHIFT_DIGITS[DIGITS[code]] if shift else DIGITS[code]
        elif code in SYM:
            ch = SYM[code]
        elif code == K_SPACE:
            ch = ' '
        if ch is not None:
            self.input['text'] += ch
            self.shift_sticky = False
        return ch is not None

    def key(self, code, value):
        """value: 1 press, 2 autorepeat, 0 release."""
        if code in (K_SHIFT, 54):
            if value == 1:
                self.shift, self.shift_alone = True, True
            elif value == 0:
                self.shift = False
                if self.shift_alone:      # tapped on its own: applies to the next key
                    self.shift_sticky = not self.shift_sticky
            return
        if value == 0:
            return
        self.shift_alone = False
        if self.screen == 'input':
            box = self.input
            if code == K_ESC and value == 1:
                self.screen = {'alias': 'settings', 'text': 'menu'}.get(box['purpose'], 'home')
            elif code == K_ENTER and value == 1:
                self.submit_input()
            elif code in (K_BACKSPACE, K_DELETE):
                box['text'] = box['text'][:-1]
            else:
                self.type_char(code)
            return
        code = NAV.get(code, code)
        repeat = value == 2
        if repeat and code not in (K_UP, K_DOWN, K_BACKSPACE):
            return
        getattr(self, 'key_' + self.screen)(code)

    def key_home(self, code):
        peers = self.core.peer_list()
        if code in (K_ESC, 41):
            self.running = False
        elif code == K_UP and peers:
            self.selected = (self.selected - 1) % len(peers)
        elif code == K_DOWN and peers:
            self.selected = (self.selected + 1) % len(peers)
        elif code in (K_ENTER, K_RIGHT) and peers:
            self.target = peers[self.selected]
            self.menu = 0
            self.screen = 'menu'
        elif code == 19:   # R
            self.rescan()
        elif code == 30:   # A
            self.ask('Add device', 'add', hint='IP address or name, optionally :port', ok='Add')
        elif code == 31:   # S
            self.settings_row = 0
            self.screen = 'settings'

    def key_menu(self, code):
        items = self.menu_items()
        if code in (K_ESC, 41, K_LEFT):
            self.screen = 'home'
        elif code == K_UP:
            self.menu = (self.menu - 1) % len(items)
        elif code == K_DOWN:
            self.menu = (self.menu + 1) % len(items)
        elif code in (K_ENTER, K_RIGHT):
            action = items[self.menu][1]
            if action == 'files':
                self.browser = None
                start = Path(self.core.config.get('last_dir') or HOME)
                self.open_browser(start if start.is_dir() else HOME)
                self.screen = 'browser'
            elif action == 'text':
                self.ask('Message', 'text', hint='to ' + self.target.alias, ok='Send')
            elif action == 'forget':
                p = self.target
                self.core.config['manual'] = [m for m in self.core.config.get('manual', [])
                                              if not (m['ip'] == p.ip and m.get('port', PORT) == p.port)]
                save_config(self.core.config)
                with self.core.lock:
                    self.core.peers.pop(p.key(), None)
                self.screen = 'home'

    def key_browser(self, code):
        b = self.browser
        entries = b['entries']
        if code in (K_ESC, 41):
            self.screen = 'menu'
        elif code == K_UP and entries:
            b['selected'] = (b['selected'] - 1) % len(entries)
        elif code == K_DOWN and entries:
            b['selected'] = (b['selected'] + 1) % len(entries)
        elif code in (K_BACKSPACE, K_LEFT):
            parent = b['dir'].parent
            if parent != b['dir']:
                previous = b['dir']
                self.open_browser(parent)
                for i, (p, _, _) in enumerate(self.browser['entries']):
                    if p == previous:
                        self.browser['selected'] = i
        elif code == K_SPACE and entries:
            path = entries[b['selected']][0]
            if path in b['marked']:
                b['marked'].remove(path)
            else:
                b['marked'].append(path)
            b['selected'] = min(b['selected'] + 1, len(entries) - 1)
        elif code in (K_ENTER, K_RIGHT) and entries:
            path, is_dir, _ = entries[b['selected']]
            if is_dir:
                self.open_browser(path)
            elif code == K_ENTER:
                marked = list(b['marked'])
                if path not in marked:
                    marked.append(path)
                self.start_send(paths=marked)
        elif code == 21 and b['marked']:   # Y
            self.start_send(paths=list(b['marked']))

    def key_incoming(self, code):
        inc = self.core.incoming
        if inc is None:
            self.screen = 'home'
            return
        if code in (21, K_ENTER):          # Y
            inc.decision = True
        elif code in (49, K_ESC):          # N
            inc.decision = False
        else:
            return
        inc.answered.set()
        with self.core.lock:
            self.core.incoming = None
        self.screen = 'transfer' if inc.decision else 'home'

    def key_transfer(self, code):
        t = self.core.transfer
        if t is None:
            self.screen = 'home'
            return
        if t.finished():
            if code in (K_ENTER, K_ESC, 41, K_SPACE):
                self.screen = 'home'
            return
        if code == K_ESC:
            if t.direction == 'send':
                abort_send(t)
            else:
                t.cancel.set()
                with self.core.lock:
                    t.state = 'cancelled'
                    t.error = 'Cancelled'
            self.toast('Cancelling…', YELLOW, 2)

    def key_message(self, code):
        alias, text, scroll = self.showing_message
        if code in (K_ENTER, K_ESC, 41):
            self.showing_message = None
            self.screen = 'home'
        elif code == K_UP:
            self.showing_message = (alias, text, max(0, scroll - 1))
        elif code == K_DOWN:
            self.showing_message = (alias, text, min(max(0, getattr(self, 'message_lines', 0) - 6), scroll + 1))
        elif code == 31:   # S
            DOWNLOADS.mkdir(parents=True, exist_ok=True)
            path = unique_path(DOWNLOADS / f'message-{time.strftime("%Y%m%d-%H%M%S")}.txt')
            path.write_text(text)
            self.toast('Saved ' + path.name, GREEN)

    def key_settings(self, code):
        rows = self.settings_rows()
        if code in (K_ESC, 41, K_LEFT):
            save_config(self.core.config)
            self.screen = 'home'
        elif code == K_UP:
            self.settings_row = (self.settings_row - 1) % len(rows)
        elif code == K_DOWN:
            self.settings_row = (self.settings_row + 1) % len(rows)
        elif code in (K_ENTER, K_RIGHT, K_SPACE):
            if self.settings_row == 0:
                self.ask('Name', 'alias', self.core.config['alias'], hint='How other devices see this Cardputer',
                         ok='Save')
            elif self.settings_row == 1:
                c = self.core.config
                c['accept'] = 'auto' if c.get('accept') != 'auto' else 'ask'
                save_config(c)

    def tick(self):
        """Switch screens for things that happened on the network."""
        core = self.core
        with core.lock:
            if core.incoming is not None and self.screen != 'incoming':
                if self.screen != 'input':
                    self.return_to = self.screen
                self.screen = 'incoming'
            if core.messages and self.screen in ('home', 'menu', 'settings', 'browser'):
                alias, text = core.messages.pop(0)
                self.showing_message = (alias, text, 0)
                self.screen = 'message'
            receive = core.receive
            if receive and receive['transfer'] is core.transfer and core.transfer.direction == 'receive':
                t = core.transfer
                if not t.finished() and time.monotonic() - t.last_activity > 90:
                    t.state, t.error = 'failed', 'The sender stopped sending'
                if not t.finished() and self.screen in ('home', 'menu', 'settings', 'browser'):
                    self.screen = 'transfer'


def probe_manual(core):
    def run():
        for m in list(core.config.get('manual', [])):
            register_with(core, m['ip'], int(m.get('port', PORT)), manual=True)
    threading.Thread(target=run, daemon=True).start()


def launcher_brightness():
    try:
        return int(json.loads(LAUNCHER_CONFIG.read_text())['brightness'])
    except (OSError, ValueError, KeyError, TypeError):
        return 60


def run():
    migrate_old_state()
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    lock = open(STATE_DIR / 'app.lock', 'w')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    core = Core()
    discovery = None
    try:
        server = Server(core)
        threading.Thread(target=server.serve_forever, daemon=True).start()
    except OSError as error:
        log('server failed', error)
        core.error = f'Port {PORT} is busy: receiving is off' if error.errno == 98 else str(error)
    try:
        discovery = Discovery(core)
        discovery.start()
        discovery.announce()
    except OSError as error:
        log('discovery failed', error)
        core.error = core.error or 'Discovery failed: ' + (error.strerror or str(error))
    probe_manual(core)
    app = App(core, discovery)
    frame = Framebuffer()
    keyboard = os.open(KEYBOARD, os.O_RDONLY | os.O_NONBLOCK)
    event = struct.Struct('llHHi')
    signal.signal(signal.SIGTERM, lambda *_: setattr(app, 'running', False))
    signal.signal(signal.SIGINT, lambda *_: setattr(app, 'running', False))
    if int(read(BACKLIGHT, '0') or 0) == 0:
        try:
            BACKLIGHT.write_text(str(launcher_brightness()))
        except OSError:
            pass
    last_announce = time.monotonic()
    try:
        while app.running:
            app.tick()
            frame.draw(app.render())
            ready, _, _ = select.select([keyboard, core.wake_r], [], [], 0.5)
            if core.wake_r in ready:
                try:
                    while os.read(core.wake_r, 4096):
                        pass
                except BlockingIOError:
                    pass
                time.sleep(0.05)      # coalesce progress updates
            if keyboard in ready:
                data = os.read(keyboard, event.size * 64)
                for offset in range(0, len(data) - event.size + 1, event.size):
                    _, _, kind, code, value = event.unpack_from(data, offset)
                    if kind == 1:
                        app.key(code, value)
            if discovery and time.monotonic() - last_announce > 60:
                discovery.send(True)    # phones that opened LocalSend later still see us
                last_announce = time.monotonic()
    finally:
        t = core.transfer
        if t is not None and not t.finished():
            abort_send(t)
        with core.lock:
            if core.incoming:
                core.incoming.decision = False
                core.incoming.answered.set()
        try:
            save_config(core.config)
        except OSError:
            pass
        os.close(keyboard)
        frame.close()
        lock.close()


def serve(args):
    """Headless test mode (no LCD, no keyboard)."""
    core = Core()
    if args.send:
        ip, what = args.send
        host, _, port = ip.partition(':')
        peer = register_with(core, host, int(port or PORT))
        if not peer:
            raise SystemExit('no answer from ' + ip)
        log('peer', peer.alias, peer.model, peer.type, peer.protocol, peer.fingerprint[:12])
        if what.startswith('text:'):
            send_job(core, peer, text=what[5:])
        else:
            send_job(core, peer, paths=[what])
        t = core.transfer
        log('result', t.state, t.error, t.done_bytes, '/', t.total)
        raise SystemExit(0 if t.state == 'done' else 1)
    server = Server(core)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    discovery = Discovery(core)
    discovery.start()
    discovery.announce()
    log('serving', PORT, 'fingerprint', core.fingerprint[:12])
    known = set()
    while True:
        time.sleep(0.2)
        with core.lock:
            if core.incoming:
                inc = core.incoming
                log('incoming', inc.info.get('alias'), [(f['name'], f['size']) for f in inc.files])
                inc.decision = args.accept == 'yes'
                core.incoming = None
                inc.answered.set()
            while core.messages:
                log('message', core.messages.pop(0))
            for p in core.peers.values():
                if p.key() not in known:
                    known.add(p.key())
                    log('peer', p.alias, p.ip, p.model, p.type)
            t = core.transfer
            if t is not None and t.finished() and not getattr(t, 'logged', False):
                t.logged = True
                log('transfer', t.direction, t.state, t.error, t.done_bytes, [str(x) for x in t.saved])


# ---------------------------------------------------------------- previews (screenshots)

def preview(screen, path):
    class FakeCore(Core):
        def __init__(self):
            self.lock = threading.RLock()
            self.config = {'alias': 'CardZero', 'accept': 'ask', 'manual': [], 'last_dir': str(HOME)}
            self.fingerprint = 'A1B2C3D4E5F6'
            self.peers, self.incoming, self.receive, self.transfer, self.messages = {}, None, None, None, []
            self.error = ''

        def wake(self):
            pass
    core = FakeCore()
    for alias, model, kind, ip in [('Pixel 8', 'Google', 'mobile', '192.168.1.23'),
                                   ('Alex’s MacBook', 'Apple', 'desktop', '192.168.1.31'),
                                   ('iPhone', 'Apple', 'mobile', '192.168.1.40'),
                                   ('Living room PC', 'Windows', 'desktop', '192.168.1.12')]:
        core.add_peer({'alias': alias, 'deviceModel': model, 'deviceType': kind, 'fingerprint': alias}, ip)
    app = App(core)
    app.addresses, app.addresses_at = [('wlan0', '192.168.1.50')], time.monotonic() + 1e9
    app.target = core.peer_list()[3]
    if screen == 'incoming':
        core.incoming = Incoming('x', {'alias': 'Pixel 8', 'deviceModel': 'Google'}, '192.168.1.23',
                                 [{'id': '1', 'name': 'IMG_2041.jpg', 'size': 3_412_000},
                                  {'id': '2', 'name': 'IMG_2042.jpg', 'size': 2_980_000},
                                  {'id': '3', 'name': 'trip-notes.pdf', 'size': 412_000},
                                  {'id': '4', 'name': 'map.png', 'size': 220_000}])
    elif screen == 'transfer':
        t = Transfer('send', 'Living room PC', [{'id': str(i), 'name': n, 'size': s} for i, (n, s) in
                     enumerate([('sketch.png', 1_200_000), ('notes.md', 8_000), ('recording.wav', 9_800_000)])])
        t.state, t.done_bytes, t.file_index, t.current, t.speed = 'sending', 6_100_000, 3, 'recording.wav', 1_420_000
        t.text = None
        core.transfer = t
    elif screen == 'message':
        app.showing_message = ('Pixel 8', 'Wi-Fi password for the workshop: maker-space-2026\nSee you at 7!', 0)
    elif screen == 'browser':
        demo = Path(os.environ.get('ZEROSEND_DEMO_DIR', HOME))
        app.open_browser(demo)
    elif screen == 'input':
        app.ask('Message', 'text', 'Running late, start without me', hint='to Living room PC', ok='Send')
    elif screen == 'empty':
        core.peers = {}
        screen = 'home'
    app.screen = screen
    app.render().save(path)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--serve', action='store_true',
                        help='test mode: network only, log to stdout, answer incoming files with --accept')
    parser.add_argument('--accept', choices=['yes', 'no'], default='yes')
    parser.add_argument('--send', nargs=2, metavar=('IP', 'PATH|text:MSG'), help='test mode: send to IP and exit')
    parser.add_argument('--preview', nargs=2, metavar=('SCREEN', 'PNG'),
                        help='render one screen (home empty menu browser input incoming transfer message settings)')
    args = parser.parse_args()
    if args.preview:
        preview(*args.preview)
    elif args.serve or args.send:
        serve(args)
    else:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        with open(STATE_DIR / 'app.log', 'w', buffering=1) as logfile:
            sys.stdout = sys.stderr = logfile
            try:
                run()
            except BlockingIOError:
                log('already running')
            except Exception:
                traceback.print_exc()
                raise SystemExit(1)
