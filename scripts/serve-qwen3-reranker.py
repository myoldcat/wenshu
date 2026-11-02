"""Loopback-only offline scoring service. No app database or credentials used."""
from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
from pathlib import Path
import signal
import sys
import threading

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server.reranker_runtime import MAX_BODY_BYTES, RuntimeFailure, RuntimeManager


def make_handler(manager):
    class Handler(BaseHTTPRequestHandler):
        server_version = 'WenshuReranker/1'

        def log_message(self, format, *args):
            pass

        def setup(self):
            super().setup()
            self.connection.settimeout(5)

        def reply(self, status, value):
            data = json.dumps(value, ensure_ascii=False).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            try:
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def do_GET(self):
            if self.path not in ('/health', '/ready'):
                self.reply(404, {'error': 'not_found'})
                return
            status = manager.status()
            self.reply(200 if self.path == '/health' or status['ready'] else 503, status)

        def do_POST(self):
            if self.path != '/v1/rerank':
                self.reply(404, {'error': 'not_found'})
                return
            try:
                if self.headers.get('Transfer-Encoding'):
                    raise RuntimeFailure('invalid_content_length', 400)
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= MAX_BODY_BYTES:
                    raise RuntimeFailure('request_too_large', 413)
                raw = self.rfile.read(length)
                if len(raw) != length:
                    raise RuntimeFailure('invalid_body', 400)
                payload = json.loads(raw)
                self.reply(200, manager.score(payload))
            except RuntimeFailure as exc:
                self.reply(exc.status, {'error': exc.code})
            except (ValueError, UnicodeDecodeError, RecursionError):
                self.reply(400, {'error': 'invalid_json'})
            except TimeoutError:
                self.reply(408, {'error': 'request_read_timeout'})

    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8788)
    parser.add_argument('--model-path', type=Path, required=True)
    parser.add_argument('--idle-seconds', type=float, default=60)
    parser.add_argument('--min-free-mib', type=int, default=10000)
    args = parser.parse_args()
    if not ipaddress.ip_address(args.host).is_loopback:
        parser.error('Only a loopback bind is permitted')
    if not 1 <= args.idle_seconds <= 600 or args.min_free_mib < 10000:
        parser.error('Use idle-seconds 1..600 and min-free-mib >=10000')
    manager = RuntimeManager(args.model_path, idle_seconds=args.idle_seconds, min_free_mib=args.min_free_mib)
    manager.verify()
    stop = threading.Event()

    def reap():
        while not stop.wait(1):
            manager.unload_if_idle()

    reaper = threading.Thread(target=reap, daemon=True)
    reaper.start()
    server = ThreadingHTTPServer((args.host, args.port), make_handler(manager))
    server.daemon_threads = True
    signal.signal(signal.SIGTERM, lambda *_: threading.Thread(target=server.shutdown, daemon=True).start())
    print(json.dumps({'event': 'ready', **manager.status()}), flush=True)
    try:
        server.serve_forever()
    finally:
        stop.set()
        server.server_close()
        manager.close()


if __name__ == '__main__':
    main()
