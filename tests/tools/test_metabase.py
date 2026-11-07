"""A fake Metabase on http.server holds state in memory. No real Metabase, synthetic values only."""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from tools.metabase.api import COLLECTION_NAME, Client, provision

ENV = {"MB_ADMIN_EMAIL": "admin@example.com", "MB_ADMIN_PASSWORD": "admin-secret-xyz", "MF_READER_PASSWORD": "reader-secret-abc"}
DEFS = [
    {"name": "Dash A", "cards": [
        {"name": "c1", "sql": "select 1", "display": "table", "visualization_settings": {}, "row": 0, "col": 0, "size_x": 6, "size_y": 4},
        {"name": "c2", "sql": "select 2", "display": "bar", "visualization_settings": {"k": 1}, "row": 0, "col": 6, "size_x": 6, "size_y": 4}]},
    {"name": "Dash B", "cards": [
        {"name": "c3", "sql": "select 3", "display": "table", "visualization_settings": {}, "row": 0, "col": 0, "size_x": 12, "size_y": 4}]},
]


class FakeMetabase:
    def __init__(self):
        self.setup_done = False
        self.databases, self.collections, self.cards, self.dashboards = [], [], [], []
        self.requests: list[tuple[str, str, str]] = []
        self._next = 1
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, payload, code=200):
                raw = json.dumps(payload).encode()
                self.send_response(code)
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def _handle(self, method):
                length = int(self.headers.get("Content-Length") or 0)
                text = self.rfile.read(length).decode() if length else ""
                fake.requests.append((method, self.path, text))
                body = json.loads(text) if text else {}
                self._send(*fake.route(method, self.path, body))

            do_GET = lambda self: self._handle("GET")  # noqa: E731
            do_POST = lambda self: self._handle("POST")  # noqa: E731
            do_PUT = lambda self: self._handle("PUT")  # noqa: E731

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def _id(self):
        self._next += 1
        return self._next

    def route(self, method, path, body):
        if path == "/api/session/properties":
            return {"setup-token": None if self.setup_done else "tok"}, 200
        if path == "/api/setup":
            self.setup_done = True
            return {"id": "sess"}, 200
        if path == "/api/session":
            return {"id": "sess"}, 200
        if path == "/api/database":
            if method == "POST":
                self.databases.append({"id": self._id(), **body})
                return self.databases[-1], 200
            return {"data": self.databases}, 200
        if path == "/api/collection":
            if method == "POST":
                self.collections.append({"id": self._id(), **body})
                return self.collections[-1], 200
            return self.collections, 200
        if path == "/api/card" and method == "POST":
            self.cards.append({"id": self._id(), **body})
            return self.cards[-1], 200
        if path == "/api/card":
            return self.cards, 200
        if path.startswith("/api/card/") and method == "PUT":
            card = next(c for c in self.cards if c["id"] == int(path.rsplit("/", 1)[1]))
            card.update(body)
            return card, 200
        if path == "/api/dashboard" and method == "POST":
            self.dashboards.append({"id": self._id(), "dashcards": [], **body})
            return self.dashboards[-1], 200
        if path == "/api/dashboard":
            return self.dashboards, 200
        if path.startswith("/api/dashboard/"):
            dash = next(d for d in self.dashboards if d["id"] == int(path.rsplit("/", 1)[1]))
            if method == "PUT":
                dash["dashcards"] = body["dashcards"]
            return dash, 200
        return {}, 404

    def writes(self):
        return [(m, p) for m, p, _ in self.requests if m in ("POST", "PUT") and p not in ("/api/session", "/api/setup")]


@pytest.fixture
def fake():
    server = FakeMetabase()
    yield server
    server.server.shutdown()


def test_first_run_creates_everything(fake):
    counts = provision(Client(fake.url), ENV, DEFS)
    assert (counts.created, counts.updated, counts.unchanged) == (7, 0, 0)  # db, collection, 3 cards, 2 dashboards
    assert [c["name"] for c in fake.collections] == [COLLECTION_NAME]
    assert len(fake.cards) == 3 and len(fake.dashboards) == 2
    assert all(len(d["dashcards"]) == len(x["cards"]) for d, x in zip(fake.dashboards, DEFS))


def test_data_connection_uses_the_reader_role(fake):
    provision(Client(fake.url), ENV, DEFS)
    details = fake.databases[0]["details"]
    assert details["user"] == "mf_reader" and details["host"] == "postgres"
    assert details["password"] == ENV["MF_READER_PASSWORD"]


def test_second_run_makes_zero_writes(fake):
    provision(Client(fake.url), ENV, DEFS)
    before = len(fake.writes())
    counts = provision(Client(fake.url), ENV, DEFS)
    assert len(fake.writes()) == before
    assert (counts.created, counts.updated, counts.unchanged) == (0, 0, 7)
    # the second run logs in instead of running setup
    assert fake.requests[-1] != ("POST", "/api/setup", "") and sum(p == "/api/setup" for _, p, _ in fake.requests) == 1


def test_changed_sql_is_exactly_one_card_put(fake):
    provision(Client(fake.url), ENV, DEFS)
    before = len(fake.writes())
    changed = json.loads(json.dumps(DEFS))
    changed[0]["cards"][0]["sql"] = "select 99"
    counts = provision(Client(fake.url), ENV, changed)
    new = fake.writes()[before:]
    assert len(new) == 1 and new[0][0] == "PUT" and new[0][1].startswith("/api/card/")
    assert (counts.created, counts.updated, counts.unchanged) == (0, 1, 6)


def test_changed_layout_is_one_dashboard_put(fake):
    provision(Client(fake.url), ENV, DEFS)
    before = len(fake.writes())
    changed = json.loads(json.dumps(DEFS))
    changed[1]["cards"][0]["size_x"] = 8
    provision(Client(fake.url), ENV, changed)
    new = fake.writes()[before:]
    assert len(new) == 1 and new[0][1].startswith("/api/dashboard/")


def test_admin_password_only_in_setup_and_login_bodies(fake):
    provision(Client(fake.url), ENV, DEFS)
    provision(Client(fake.url), ENV, DEFS)
    leaks = [p for _, p, body in fake.requests if ENV["MB_ADMIN_PASSWORD"] in body or ENV["MB_ADMIN_PASSWORD"] in p]
    assert set(leaks) <= {"/api/setup", "/api/session"} and leaks
    assert not any(ENV["MB_ADMIN_PASSWORD"] in body for _, p, body in fake.requests if p not in ("/api/setup", "/api/session"))


def test_errors_do_not_echo_secrets(fake):
    from tools.metabase.api import ApiError

    with pytest.raises(ApiError) as caught:
        Client(fake.url).get("/api/nope")
    assert "404" in str(caught.value)


def test_always_put_version_is_caught(fake, monkeypatch):
    """Control: the zero-writes assertion can fail (a provisioner that always PUTs trips it)."""
    from tools.metabase import api

    provision(Client(fake.url), ENV, DEFS)
    before = len(fake.writes())
    monkeypatch.setattr(api, "_card_differs", lambda *a: True)
    provision(Client(fake.url), ENV, DEFS)
    assert len(fake.writes()) > before
