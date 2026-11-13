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
            self.dashboards.append({"id": self._id(), "dashcards": [], "parameters": [], **body})
            return self.dashboards[-1], 200
        if path == "/api/dashboard":
            return self.dashboards, 200
        if path.startswith("/api/dashboard/"):
            dash = next(d for d in self.dashboards if d["id"] == int(path.rsplit("/", 1)[1]))
            if method == "PUT":
                dash["dashcards"] = body["dashcards"]  # stored as sent, parameter_mappings included
                if "parameters" in body:
                    dash["parameters"] = body["parameters"]
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


def test_api_error_carries_metabase_message_but_never_the_request_password(fake, monkeypatch):
    from tools.metabase.api import ApiError

    monkeypatch.setattr(FakeMetabase, "route", lambda self, m, p, b: ({"errors": {"password": "password is too common"}}, 400))
    with pytest.raises(ApiError) as caught:
        Client(fake.url).post("/api/setup", {"user": {"password": "request-secret-xyz"}})
    assert "password is too common" in str(caught.value) and "400" in str(caught.value)
    assert "request-secret-xyz" not in str(caught.value)


def test_api_error_truncates_a_long_body(fake, monkeypatch):
    from tools.metabase.api import ApiError

    monkeypatch.setattr(FakeMetabase, "route", lambda self, m, p, b: ({"message": "x" * 5000}, 500))
    with pytest.raises(ApiError) as caught:
        Client(fake.url).get("/api/card")
    assert len(str(caught.value)) < 500


def test_every_dashboard_card_variable_has_a_template_tag():
    import re
    from pathlib import Path

    from tools.metabase.api import _card_body, load_definitions

    directory = Path(__file__).resolve().parents[2] / "deploy" / "metabase" / "dashboards"
    seen = 0
    for definition in load_definitions(directory):
        for card in definition["cards"]:
            body = _card_body(card, 1, 2, definition.get("parameters"))
            tags = body["dataset_query"]["native"].get("template-tags", {})
            for var in re.findall(r"\{\{\s*(\w+)\s*\}\}", card["sql"]):
                seen += 1
                assert var in tags and tags[var]["name"] == var and tags[var]["type"], (card["name"], var)
            assert set(tags) == set(re.findall(r"\{\{\s*(\w+)\s*\}\}", card["sql"]))
    assert seen >= 4


def test_declared_dashboard_parameter_type_is_used_for_the_tag():
    from tools.metabase.api import _card_body

    card = {"name": "c", "sql": "select 1 [[where d = {{day}}]]", "display": "table"}
    body = _card_body(card, 1, 2, [{"name": "day", "type": "date"}])
    assert body["dataset_query"]["native"]["template-tags"]["day"]["type"] == "date"


class EchoingMetabase(FakeMetabase):
    """Stores cards the way the real one does: adds ids and extra keys, rewrites template-tag ids and display names."""

    def route(self, method, path, body):
        if path.startswith("/api/card") and method in ("POST", "PUT") and body:
            body = json.loads(json.dumps(body))
            body["entity_id"] = "ent-" + body["name"]
            body["archived"] = False
            body["visualization_settings"] = {**body["visualization_settings"], "table.cell_column": "x"}
            native = body["dataset_query"]["native"]
            native["template-tags"] = {
                k: {**t, "id": "server-" + k, "display-name": t["display-name"].lower(), "required": False}
                for k, t in native.get("template-tags", {}).items()}
        return super().route(method, path, body)


def test_second_provision_of_unchanged_json_updates_nothing_against_an_echoing_metabase():
    fake = EchoingMetabase()
    try:
        defs = json.loads(json.dumps(DEFS))
        defs[0]["cards"][0]["sql"] = "select 1 [[where a = {{a_var}}]]"
        provision(Client(fake.url), ENV, defs)
        before = len(fake.writes())
        counts = provision(Client(fake.url), ENV, defs)
        assert counts.updated == 0 and len(fake.writes()) == before
        defs[0]["cards"][0]["sql"] = "select 2 [[where a = {{a_var}}]]"
        assert provision(Client(fake.url), ENV, defs).updated == 1
    finally:
        fake.server.shutdown()


class V065Metabase(FakeMetabase):
    """Stores cards in the v0.65 MBQL-lib shape: stages[0].native is the SQL string, template-tags a list, absent when empty."""

    def route(self, method, path, body):
        result, code = super().route(method, path, body)
        if path.startswith("/api/card") and method in ("POST", "PUT") and body:
            native = body["dataset_query"]["native"]
            stage = {"lib/type": "mbql.stage/native", "native": native["query"]}
            if native.get("template-tags"):
                stage["template-tags"] = [{"id": "server-" + k, "name": k, "display-name": k, "type": t["type"]}
                                          for k, t in native["template-tags"].items()]
            card = next(c for c in self.cards if c["id"] == result["id"])
            card["dataset_query"] = {"lib/type": "mbql/query", "database": body["dataset_query"]["database"], "stages": [stage]}
        if path.startswith("/api/dashboard/") and method == "PUT":
            # checkpoint 1 on v0.65.3: parameters and mappings come back as sent, plus keys the server adds
            result["parameters"] = [{**p, "sectionId": "string"} for p in result["parameters"]]
            result["dashcards"] = [{**d, "entity_id": f"ent-{d['card_id']}", "parameter_mappings": [dict(m) for m in d.get("parameter_mappings", [])]}
                                   for d in result["dashcards"]]
        return result, code


def _tagged_defs(sql):
    defs = json.loads(json.dumps(DEFS))
    defs[0]["cards"][0]["sql"] = sql  # c1 has a tag, c2 and c3 have none
    return defs


def test_v065_stored_shape_is_unchanged_on_second_run_and_changed_sql_is_one_update():
    fake = V065Metabase()
    try:
        defs = _tagged_defs("select 1 [[where a = {{a_var}}]]")
        provision(Client(fake.url), ENV, defs)
        assert isinstance(fake.cards[0]["dataset_query"]["stages"][0]["template-tags"], list)
        assert "template-tags" not in fake.cards[1]["dataset_query"]["stages"][0]
        before = len(fake.writes())
        counts = provision(Client(fake.url), ENV, defs)
        assert (counts.updated, len(fake.writes())) == (0, before)
        defs[0]["cards"][0]["sql"] = "select 2 [[where a = {{a_var}}]]"
        defs[0]["cards"][1]["sql"] = "select 22"
        counts = provision(Client(fake.url), ENV, defs)
        assert [e for e in counts.log if e.startswith("card ")] == ["card c1: updated", "card c2: updated", "card c3: unchanged"]
    finally:
        fake.server.shutdown()


def test_legacy_stored_shape_stays_pinned(fake):
    defs = _tagged_defs("select 1 [[where a = {{a_var}}]]")
    provision(Client(fake.url), ENV, defs)
    assert isinstance(fake.cards[0]["dataset_query"]["native"]["template-tags"], dict)
    assert provision(Client(fake.url), ENV, defs).updated == 0
    defs[0]["cards"][0]["sql"] = "select 9"
    assert provision(Client(fake.url), ENV, defs).updated == 1


def test_run_prints_one_line_per_object_before_the_totals(fake, tmp_path, capsys):
    from tools.metabase import main

    (tmp_path / "deploy" / "metabase" / "dashboards").mkdir(parents=True)
    for i, d in enumerate(DEFS):
        (tmp_path / "deploy" / "metabase" / "dashboards" / f"{i}.json").write_text(json.dumps(d))
    port = fake.url.rsplit(":", 1)[1]
    (tmp_path / ".env").write_text("".join(f"{k}={v}\n" for k, v in {**ENV, "MF_MB_PORT": port}.items()))
    assert main(root=tmp_path) == 0
    capsys.readouterr()
    assert main(root=tmp_path) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[:2] == ["metabase: database moneyflow: unchanged", f"metabase: collection {COLLECTION_NAME}: unchanged"]
    for line in ("card c1: unchanged", "card c2: unchanged", "card c3: unchanged", "dashboard Dash A: unchanged", "dashboard Dash B: unchanged"):
        assert "metabase: " + line in out
    assert out[-1] == "metabase: created 0, updated 0, unchanged 7" and len(out) == 8
    assert "secret" not in "".join(out)


@pytest.fixture(params=[FakeMetabase, V065Metabase], ids=["legacy", "v065"])
def any_fake(request):
    server = request.param()
    yield server
    server.server.shutdown()


def _param_defs(declared=None):
    """Dash A: c1 uses the tag a_var, c2 uses none."""
    defs = _tagged_defs("select 1 [[where a = {{a_var}}]]")
    defs[0]["parameters"] = [{"name": "a_var", "type": "text"}] if declared is None else declared
    return defs


def _dash(fake, name="Dash A"):
    return next(d for d in fake.dashboards if d["name"] == name)


def test_dashboard_is_created_with_parameters_and_per_card_mappings(any_fake):
    provision(Client(any_fake.url), ENV, _param_defs())
    dash = _dash(any_fake)
    (param,) = dash["parameters"]
    assert (param["slug"], param["name"], param["type"]) == ("a_var", "A var", "string/=") and param["id"]
    c1, c2 = dash["dashcards"]
    assert c1["parameter_mappings"] == [{"parameter_id": param["id"], "card_id": c1["card_id"], "target": ["variable", ["template-tag", "a_var"]]}]
    assert not c2.get("parameter_mappings")  # c2 does not use the tag
    assert not _dash(any_fake, "Dash B")["parameters"]


def test_second_provision_of_a_parameterised_dashboard_is_unchanged(any_fake):
    provision(Client(any_fake.url), ENV, _param_defs())
    before = len(any_fake.writes())
    counts = provision(Client(any_fake.url), ENV, _param_defs())
    assert len(any_fake.writes()) == before and counts.updated == 0
    assert "dashboard Dash A: unchanged" in counts.log


def test_a_stored_parameter_type_change_is_one_dashboard_update(any_fake):
    provision(Client(any_fake.url), ENV, _param_defs())
    _dash(any_fake)["parameters"][0]["type"] = "number/="
    counts = provision(Client(any_fake.url), ENV, _param_defs())
    assert counts.log.count("dashboard Dash A: updated") == 1 and counts.updated == 1
    assert _dash(any_fake)["parameters"][0]["type"] == "string/="


def test_a_missing_stored_mapping_is_one_dashboard_update(any_fake):
    provision(Client(any_fake.url), ENV, _param_defs())
    _dash(any_fake)["dashcards"][0]["parameter_mappings"] = []
    counts = provision(Client(any_fake.url), ENV, _param_defs())
    assert counts.log.count("dashboard Dash A: updated") == 1 and counts.updated == 1
    assert _dash(any_fake)["dashcards"][0]["parameter_mappings"]


def test_tag_type_change_alone_updates_the_card(any_fake):
    """Guards _tag_view: a card whose only change is one template-tag type must be PUT."""
    provision(Client(any_fake.url), ENV, _param_defs())
    counts = provision(Client(any_fake.url), ENV, _param_defs([{"name": "a_var", "type": "number"}]))
    assert "card c1: updated" in counts.log and "card c2: unchanged" in counts.log
    query = next(c for c in any_fake.cards if c["name"] == "c1")["dataset_query"]
    tags = query["stages"][0]["template-tags"] if "stages" in query else list(query["native"]["template-tags"].values())
    assert [t["type"] for t in tags] == ["number"]


def _write_definition(tmp_path, definition):
    (tmp_path / "d.json").write_text(json.dumps(definition))
    return tmp_path


def _definition(parameters, sql="select 1 [[where a = {{a_var}}]]"):
    return {"name": "D", "parameters": parameters, "cards": [{"name": "c", "sql": sql, "display": "table", "row": 0, "col": 0, "size_x": 1, "size_y": 1}]}


@pytest.mark.parametrize("definition, needle", [
    (_definition([]), "a_var"),                                                              # tag not declared
    (_definition([{"name": "a_var", "type": "text"}, {"name": "unused", "type": "text"}]), "unused"),  # declared, no card uses it
    (_definition([{"name": "a_var", "type": "date"}]), "date"),                              # unknown type
], ids=["undeclared-tag", "unused-parameter", "unknown-type"])
def test_load_definitions_refuses_a_bad_parameter_setup(tmp_path, definition, needle):
    from tools.metabase.api import load_definitions

    with pytest.raises(ValueError) as caught:
        load_definitions(_write_definition(tmp_path, definition))
    assert "d.json" in str(caught.value) and needle in str(caught.value)


def test_load_definitions_accepts_a_consistent_setup(tmp_path):
    from tools.metabase.api import load_definitions

    assert len(load_definitions(_write_definition(tmp_path, _definition([{"name": "a_var", "type": "text"}])))) == 1
