"""Local fake servers for end-to-end tests (fake Claude API, fake TLS SMTP server).

They use the TEST-ONLY certificates in tests/fixtures.  Python 3.8 compatible,
because these also run inside the 32-bit Windows Python under test.
"""

import base64
import json
import os
import socketserver
import ssl
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
TEST_CA = os.path.join(FIXTURES, "test-ca.pem")


def server_ssl_context():
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(os.path.join(FIXTURES, "localhost.pem"), os.path.join(FIXTURES, "localhost.key"))
    return ctx


class _ThreadingHTTPServer(socketserver.ThreadingMixIn, HTTPServer):
    daemon_threads = True


class FakeAPI(object):
    """Scripted stand-in for api.anthropic.com (and the OpenAI images endpoint).

    `responses` is a list of (status, body_dict) or callables taking the request body.
    """

    def __init__(self, responses=None, tls=False):
        self.responses = list(responses or [])
        self.requests = []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
                fake.requests.append({"path": self.path, "headers": dict((k.lower(), v) for k, v in self.headers.items()), "body": body})
                if fake.responses:
                    item = fake.responses.pop(0)
                    status, payload = item(body) if callable(item) else item
                else:
                    status, payload = 500, {"error": {"message": "no scripted response left"}}
                data = json.dumps(payload).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                if status == 429:
                    self.send_header("retry-after", "1")
                self.end_headers()
                self.wfile.write(data)

        self.server = _ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        if tls:
            self.server.socket = server_ssl_context().wrap_socket(self.server.socket, server_side=True)
        self.port = self.server.server_address[1]
        self.url = "%s://%s:%d" % ("https" if tls else "http", "localhost" if tls else "127.0.0.1", self.port)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class FakeFiles(object):
    """Serves fixed files over HTTPS, like GitHub's release downloads.  `files` maps path -> bytes."""

    def __init__(self, files=None):
        self.files = dict(files or {})
        self.requests = []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                fake.requests.append(self.path)
                data = fake.files.get(self.path)
                self.send_response(200 if data is not None else 404)
                data = data if data is not None else b"Not Found"
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.server = _ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.socket = server_ssl_context().wrap_socket(self.server.socket, server_side=True)
        self.url = "https://localhost:%d" % self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def text_reply(text):
    return 200, {"content": [{"type": "text", "text": text}], "stop_reason": "end_turn"}


def tool_reply(name, tool_input, tool_id="toolu_1"):
    return 200, {"content": [{"type": "thinking", "thinking": "", "signature": "sig"},
                             {"type": "tool_use", "id": tool_id, "name": name, "input": tool_input}],
                 "stop_reason": "tool_use"}


class FakeSMTP(object):
    """Minimal implicit-TLS SMTP server (like smtp.gmail.com:465) that stores received mail."""

    def __init__(self, username="from@example.com", password="abcdefghijklmnop"):
        self.messages = []
        self.logins = []
        fake = self
        ctx = server_ssl_context()

        class Handler(socketserver.StreamRequestHandler):
            def send(self, line):
                self.wfile.write((line + "\r\n").encode("ascii"))
                self.wfile.flush()

            def handle(self):
                self.send("220 fake.smtp ready")
                mail = {}
                while True:
                    raw = self.rfile.readline()
                    if not raw:
                        return
                    line = raw.decode("utf-8", "replace").rstrip("\r\n")
                    cmd = line.split(" ")[0].upper()
                    if cmd in ("EHLO", "HELO"):
                        self.wfile.write(b"250-fake.smtp\r\n250-AUTH PLAIN LOGIN\r\n250 8BITMIME\r\n")
                        self.wfile.flush()
                    elif cmd == "AUTH":
                        parts = line.split(" ")
                        if parts[1].upper() == "PLAIN":
                            token = parts[2] if len(parts) > 2 else None
                            if token is None:
                                self.send("334 ")
                                token = self.rfile.readline().decode().strip()
                            _, user, pwd = base64.b64decode(token).decode("utf-8").split("\0")
                        else:
                            self.send("334 VXNlcm5hbWU6")
                            user = base64.b64decode(self.rfile.readline().strip()).decode()
                            self.send("334 UGFzc3dvcmQ6")
                            pwd = base64.b64decode(self.rfile.readline().strip()).decode()
                        fake.logins.append((user, pwd))
                        if user == username and pwd == password:
                            self.send("235 2.7.0 Accepted")
                        else:
                            self.send("535 5.7.8 Username and Password not accepted")
                    elif cmd == "MAIL":
                        mail = {"from": line, "to": [], "data": b""}
                        self.send("250 OK")
                    elif cmd == "RCPT":
                        mail["to"].append(line)
                        self.send("250 OK")
                    elif cmd == "DATA":
                        self.send("354 Go ahead")
                        chunks = []
                        while True:
                            data_line = self.rfile.readline()
                            if data_line in (b".\r\n", b".\n", b""):
                                break
                            chunks.append(data_line)
                        mail["data"] = b"".join(chunks)
                        fake.messages.append(mail)
                        self.send("250 OK queued")
                    elif cmd == "QUIT":
                        self.send("221 Bye")
                        return
                    else:
                        self.send("250 OK")

        class Server(socketserver.ThreadingTCPServer):
            daemon_threads = True
            allow_reuse_address = True

            def get_request(self):
                sock, addr = socketserver.ThreadingTCPServer.get_request(self)
                return ctx.wrap_socket(sock, server_side=True), addr

        self.server = Server(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()
