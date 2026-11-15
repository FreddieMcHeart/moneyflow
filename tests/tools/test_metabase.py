"""A fake Metabase on http.server holds state in memory. No real Metabase, synthetic values only."""

import json
import re
import threading
import uuid
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


BASE_TYPES = {"Category": "type/Text", "Subcategory": "type/Text", "Currency": "type/Text",
              "Plan": "type/Decimal", "Actual": "type/Decimal", "Transactions": "type/BigInteger"}


def _stored_sql(card):
    """The SQL string of a stored native card, in either stored shape; None for a query card."""
    query = card.get("dataset_query") or {}
    if "stages" in query:
        return query["stages"][0].get("native")
    return (query.get("native") or {}).get("query")


def _metadata_for(card):
    """A fake rule: one result column per double-quoted SELECT alias (`as "Plan"`), base type by name."""
    return [{"name": name, "base_type": BASE_TYPES.get(name, "type/Text")} for name in re.findall(r'as "(\w+)"', _stored_sql(card) or "")]


def _stages_query(dataset_query, source_id):
    """The v0.65 stored shape of a query-builder card (T5 step 1 (a)): no top-level type, field name LAST, random uuids."""
    def field(ref):
        name, options = ref[1], ref[2]
        return ["field", {"base-type": options["base-type"], "lib/uuid": str(uuid.uuid4())}, name]

    query = dataset_query["query"]
    stage = {"lib/type": "mbql.stage/mbql", "source-card": source_id,
             "breakout": [field(ref) for ref in query["breakout"]],
             "aggregation": [[op, {"lib/uuid": str(uuid.uuid4())}, field(ref)] for op, ref in query["aggregation"]]}
    return {"lib/type": "mbql/query", "database": dataset_query["database"], "stages": [stage]}


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

    def metadata(self, card):
        return _metadata_for(card)

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
        if path.startswith("/api/collection/") and path.endswith("/items"):
            collection_id = int(path.split("/")[3])
            items = [{"id": c["id"], "name": c["name"], "model": "card"} for c in self.cards
                     if c.get("collection_id") == collection_id and not c.get("archived")]
            items += [{"id": d["id"], "name": d["name"], "model": "dashboard"} for d in self.dashboards
                      if d.get("collection_id") == collection_id and not d.get("archived")]
            items += [i for i in getattr(self, "other_items", []) if i["collection_id"] == collection_id]
            return {"data": items}, 200
        if path == "/api/card" and method == "POST":
            self.cards.append({"id": self._id(), **body})
            return self.cards[-1], 200
        if path == "/api/card":
            return [c for c in self.cards if not c.get("archived")], 200
        if path.startswith("/api/card/") and path.endswith("/query") and method == "POST":
            return {"data": {"rows": []}}, 200
        if path.startswith("/api/card/") and method == "GET":
            card = next(c for c in self.cards if c["id"] == int(path.rsplit("/", 1)[1]))
            return {**card, "result_metadata": self.metadata(card)}, 200
        if path.startswith("/api/card/") and method == "PUT":
            card = next(c for c in self.cards if c["id"] == int(path.rsplit("/", 1)[1]))
            card.update(body)
            return card, 200
        if path == "/api/dashboard" and method == "POST":
            self.dashboards.append({"id": self._id(), "dashcards": [], "parameters": [], **body})
            return self.dashboards[-1], 200
        if path == "/api/dashboard":
            return [d for d in self.dashboards if not d.get("archived")], 200
        if path.startswith("/api/dashboard/"):
            dash = next(d for d in self.dashboards if d["id"] == int(path.rsplit("/", 1)[1]))
            if method == "PUT":
                if "dashcards" in body:
                    dash["dashcards"] = body["dashcards"]  # stored as sent, parameter_mappings included
                if "archived" in body:
                    dash["archived"] = body["archived"]
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
            if "sql" not in card:
                continue
            body = _card_body(card, 1, 2, definition.get("parameters"))
            tags = body["dataset_query"]["native"].get("template-tags", {})
            for var in re.findall(r"\{\{\s*(\w+)\s*\}\}", card["sql"]):
                seen += 1
                assert var in tags and tags[var]["name"] == var and tags[var]["type"], (card["name"], var)
            assert set(tags) == set(re.findall(r"\{\{\s*(\w+)\s*\}\}", card["sql"]))
    assert seen >= 2


def test_declared_dashboard_parameter_type_is_used_for_the_tag():
    from tools.metabase.api import _card_body

    card = {"name": "c", "sql": "select 1 [[where d = {{day}}]]", "display": "table"}
    body = _card_body(card, 1, 2, [{"name": "day", "type": "date"}])
    assert body["dataset_query"]["native"]["template-tags"]["day"]["type"] == "date"


class EchoingMetabase(FakeMetabase):
    """Stores cards the way the real one does: adds ids and extra keys, rewrites template-tag ids and display names."""

    def route(self, method, path, body):
        if path.startswith("/api/card") and method in ("POST", "PUT") and body and "dataset_query" in body:
            body = json.loads(json.dumps(body))
            body["entity_id"] = "ent-" + body["name"]
            body["archived"] = False
            body["visualization_settings"] = {**body["visualization_settings"], "table.cell_column": "x"}
            if body["dataset_query"]["type"] == "query":
                source_id = int(body["dataset_query"]["query"]["source-table"].removeprefix("card__"))
                body["dataset_query"] = _stages_query(body["dataset_query"], source_id)
            else:
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
        if path.startswith("/api/card") and method in ("POST", "PUT") and "dataset_query" in body:
            card = next(c for c in self.cards if c["id"] == result["id"])
            if body["dataset_query"]["type"] == "query":
                source_id = int(body["dataset_query"]["query"]["source-table"].removeprefix("card__"))
                card["dataset_query"] = _stages_query(body["dataset_query"], source_id)
            else:
                native = body["dataset_query"]["native"]
                stage = {"lib/type": "mbql.stage/native", "native": native["query"]}
                if native.get("template-tags"):
                    stage["template-tags"] = [{"id": "server-" + k, "name": k, "display-name": k, "type": t["type"]}
                                              for k, t in native["template-tags"].items()]
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
    assert out[-1] == "metabase: created 0, updated 0, unchanged 7, archived 0" and len(out) == 8
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


def _collection_id(fake):
    return next(c["id"] for c in fake.collections if c["name"] == COLLECTION_NAME)


def _seed_stale(fake, collection_id=None):
    """A card and a dashboard no definition names, placed in the provisioned collection unless told otherwise."""
    collection_id = _collection_id(fake) if collection_id is None else collection_id
    fake.cards.append({"id": fake._id(), "name": "stale card", "collection_id": collection_id})
    fake.dashboards.append({"id": fake._id(), "name": "stale dash", "collection_id": collection_id, "dashcards": [], "parameters": []})


def _archive_puts(fake):
    return [(m, p) for m, p, body in fake.requests if m == "PUT" and '"archived"' in body]


def test_stale_card_and_dashboard_are_archived(fake):
    provision(Client(fake.url), ENV, DEFS)
    _seed_stale(fake)
    before = len(fake.writes())
    counts = provision(Client(fake.url), ENV, DEFS)
    assert all(x.get("archived") is True for x in (*fake.cards[-1:], *fake.dashboards[-1:]))
    assert len(fake.writes()) - before == 2 and len(_archive_puts(fake)) == 2
    assert counts.archived == 2 and "archived 2" in counts.line()
    assert "card stale card: archived" in counts.log and "dashboard stale dash: archived" in counts.log


def test_second_run_after_archive_writes_nothing(fake):
    provision(Client(fake.url), ENV, DEFS)
    _seed_stale(fake)
    provision(Client(fake.url), ENV, DEFS)
    before = len(fake.writes())
    counts = provision(Client(fake.url), ENV, DEFS)
    assert len(fake.writes()) == before
    assert counts.archived == 0 and "archived 0" in counts.line()


def test_same_name_in_another_collection_is_not_archived(fake):
    provision(Client(fake.url), ENV, DEFS)
    other = fake._id()
    _seed_stale(fake, collection_id=other)
    before = len(fake.writes())
    counts = provision(Client(fake.url), ENV, DEFS)
    assert not any(x.get("archived") for x in (*fake.cards, *fake.dashboards))
    assert len(fake.writes()) == before and not _archive_puts(fake)
    assert counts.archived == 0


def test_other_models_in_the_collection_are_left_alone(fake):
    provision(Client(fake.url), ENV, DEFS)
    cid = _collection_id(fake)
    fake.other_items = [{"id": fake._id(), "name": m + " item", "model": m, "collection_id": cid}
                        for m in ("dataset", "metric", "collection")]
    counts = provision(Client(fake.url), ENV, DEFS)
    assert not _archive_puts(fake) and counts.archived == 0


def test_defined_items_are_never_archived(fake):
    provision(Client(fake.url), ENV, DEFS)
    counts = provision(Client(fake.url), ENV, DEFS)
    assert not _archive_puts(fake) and not any(x.get("archived") for x in (*fake.cards, *fake.dashboards))
    assert counts.archived == 0 and "archived 0" in counts.line()


def test_v065_second_provision_reports_zero_updated_and_archived():
    fake = V065Metabase()
    try:
        provision(Client(fake.url), ENV, DEFS)
        _seed_stale(fake)
        first = provision(Client(fake.url), ENV, DEFS)
        assert first.archived == 2
        before = len(fake.writes())
        second = provision(Client(fake.url), ENV, DEFS)
        assert (second.updated, second.archived, len(fake.writes())) == (0, 0, before)
    finally:
        fake.server.shutdown()


# ---- T5: pivot cards (fabricated names and values only) ----

ROWS_SQL = ('select \'a\' as "Category", \'b\' as "Subcategory", \'UAH\' as "Currency", 1 as "Plan", 2 as "Actual", 3 as "Transactions"')
SPLIT_KEYS = ('["name","sum"]', '["name","sum_2"]', '["name","sum_3"]')
PIVOT_SETTINGS = {
    "pivot.show_row_totals": False,
    "column_settings": {
        SPLIT_KEYS[0]: {"column_title": "Plan", "number_style": "decimal", "decimals": 2},
        SPLIT_KEYS[1]: {"column_title": "Actual", "number_style": "decimal", "decimals": 2},
        SPLIT_KEYS[2]: {"column_title": "Transactions"},
    },
}
PIVOT = {"source": "rows card", "rows": ["Category", "Subcategory"], "columns": ["Currency"], "values": ["Plan", "Actual", "Transactions"]}
PIVOT_DEFS = [{"name": "Dash P", "parameters": [], "cards": [
    {"name": "rows card", "sql": ROWS_SQL, "display": "table", "visualization_settings": {}, "on_dashboard": False},
    {"name": "pivot card", "pivot": PIVOT, "display": "pivot", "visualization_settings": PIVOT_SETTINGS,
     "row": 0, "col": 0, "size_x": 24, "size_y": 8},
    {"name": "plain", "sql": "select 1", "display": "table", "visualization_settings": {}, "row": 8, "col": 0, "size_x": 24, "size_y": 6}]}]
METADATA = [{"name": n, "base_type": t} for n, t in
            (("Category", "type/Text"), ("Subcategory", "type/Text"), ("Currency", "type/Text"),
             ("Plan", "type/Float"), ("Actual", "type/Decimal"), ("Transactions", "type/BigInteger"))]


def _pivot_defs(**changes):
    defs = json.loads(json.dumps(PIVOT_DEFS))
    defs[0]["cards"][1]["pivot"].update(changes)
    return defs


def _pivot_card(fake, name="pivot card"):
    return next(c for c in fake.cards if c["name"] == name)


def _write_pivot_definition(tmp_path, cards, parameters=()):
    (tmp_path / "d.json").write_text(json.dumps({"name": "D", "parameters": list(parameters), "cards": cards}))
    return tmp_path


def _sql_card(name="rows card", sql=ROWS_SQL, **extra):
    return {"name": name, "sql": sql, "display": "table", "row": 0, "col": 0, "size_x": 1, "size_y": 1, **extra}


def _pivot_json_card(name="pivot card", **extra):
    return {"name": name, "pivot": dict(PIVOT), "display": "pivot", "row": 0, "col": 0, "size_x": 1, "size_y": 1,
            "visualization_settings": {}, **extra}


@pytest.mark.parametrize("cards, needle", [
    ([{**_sql_card(), "pivot": PIVOT}], "exactly one of sql or pivot"),
    ([{k: v for k, v in _pivot_json_card().items() if k != "pivot"}], "exactly one of sql or pivot"),
    ([_pivot_json_card()], "pivot source"),                                   # no earlier sql card of that name
    ([_pivot_json_card(), _sql_card()], "pivot source"),                      # the source comes later in the file
    ([_sql_card(), _pivot_json_card(visualization_settings={"pivot_table.column_split": {"rows": []}})], "column_split"),
], ids=["both", "neither", "unknown-source", "later-source", "column-split-written"])
def test_load_definitions_refuses_a_bad_pivot_card(tmp_path, cards, needle):
    from tools.metabase.api import load_definitions

    with pytest.raises(ValueError) as caught:
        load_definitions(_write_pivot_definition(tmp_path, cards))
    assert "d.json" in str(caught.value) and needle in str(caught.value)


def test_load_definitions_refuses_a_pivot_on_a_dashboard_with_parameters(tmp_path):
    from tools.metabase.api import load_definitions

    cards = [_sql_card(sql=ROWS_SQL + " [[where 1 = {{a_var}}]]"), _pivot_json_card()]
    with pytest.raises(ValueError) as caught:
        load_definitions(_write_pivot_definition(tmp_path, cards, [{"name": "a_var", "type": "text"}]))
    assert "pivot on a dashboard with parameters" in str(caught.value)


def test_load_definitions_accepts_a_pivot_after_its_sql_source_and_off_dashboard_cards(tmp_path):
    from tools.metabase.api import load_definitions

    cards = [{"name": "rows card", "sql": ROWS_SQL, "display": "table", "on_dashboard": False}, _pivot_json_card()]
    assert len(load_definitions(_write_pivot_definition(tmp_path, cards))) == 1


def test_a_pivot_card_body_is_a_query_over_the_source_card_with_types_from_its_metadata():
    from tools.metabase.api import _card_body

    body = _card_body(PIVOT_DEFS[0]["cards"][1], 1, 2, None, source=(77, METADATA))
    assert body["dataset_query"] == {"type": "query", "database": 1, "query": {
        "source-table": "card__77",
        "breakout": [["field", "Category", {"base-type": "type/Text"}], ["field", "Subcategory", {"base-type": "type/Text"}],
                     ["field", "Currency", {"base-type": "type/Text"}]],
        "aggregation": [["sum", ["field", "Plan", {"base-type": "type/Float"}]],
                        ["sum", ["field", "Actual", {"base-type": "type/Decimal"}]],
                        ["sum", ["field", "Transactions", {"base-type": "type/BigInteger"}]]]}}
    assert (body["name"], body["collection_id"], body["display"]) == ("pivot card", 2, "pivot")


def test_the_provisioner_builds_column_split_and_keeps_the_card_settings():
    from tools.metabase.api import _card_body

    body = _card_body(PIVOT_DEFS[0]["cards"][1], 1, 2, None, source=(77, METADATA))
    settings = body["visualization_settings"]
    assert settings["pivot_table.column_split"] == {"rows": ["Category", "Subcategory"], "columns": ["Currency"],
                                                    "values": ["sum", "sum_2", "sum_3"]}
    assert settings["pivot.show_row_totals"] is False and settings["column_settings"] == PIVOT_SETTINGS["column_settings"]
    assert "pivot_table.column_split" not in PIVOT_DEFS[0]["cards"][1]["visualization_settings"]  # the input is not mutated


@pytest.mark.parametrize("source, needle", [
    ((77, []), "no result_metadata"),
    ((77, None), "no result_metadata"),
    ((77, [m for m in METADATA if m["name"] != "Actual"]), "Actual"),
], ids=["empty-metadata", "no-metadata", "missing-name"])
def test_a_pivot_card_body_without_metadata_or_a_named_column_raises_with_the_card_name(source, needle):
    from tools.metabase.api import _card_body

    with pytest.raises(ValueError) as caught:
        _card_body(PIVOT_DEFS[0]["cards"][1], 1, 2, None, source=source)
    assert "pivot card" in str(caught.value) and needle in str(caught.value)


def _wanted():
    from tools.metabase.api import _card_body

    return _card_body(PIVOT_DEFS[0]["cards"][1], 1, 2, None, source=(77, METADATA))


def _stored(wanted, shape):
    """What the server holds for the wanted card: the legacy shape as sent, or the v0.65 `stages` shape."""
    query = wanted["dataset_query"] if shape == "legacy" else _stages_query(wanted["dataset_query"], 77)
    return {"id": 5, "display": wanted["display"], "visualization_settings": {**wanted["visualization_settings"], "extra": 1},
            "dataset_query": query}


@pytest.mark.parametrize("shape", ["legacy", "stages"])
def test_card_differs_is_false_for_a_stored_pivot_in_either_shape(shape):
    from tools.metabase.api import _card_differs

    wanted = _wanted()
    assert not _card_differs(_stored(wanted, shape), wanted)


def _mutate(wanted, how):
    wanted = json.loads(json.dumps(wanted))
    query = wanted["dataset_query"]["query"]
    if how == "source":
        query["source-table"] = "card__78"
    elif how == "breakout-order":
        query["breakout"].reverse()
    elif how == "breakout-name":
        query["breakout"][0][1] = "Other"
    elif how == "aggregation-op":
        query["aggregation"][0][0] = "avg"
    elif how == "aggregation-field":
        query["aggregation"][1][1][1] = "Plan"
    elif how == "display":
        wanted["display"] = "table"
    elif how == "setting":
        wanted["visualization_settings"]["pivot.show_row_totals"] = True
    return wanted


@pytest.mark.parametrize("shape", ["legacy", "stages"])
@pytest.mark.parametrize("how", ["source", "breakout-order", "breakout-name", "aggregation-op", "aggregation-field", "display", "setting"])
def test_card_differs_sees_every_change_of_a_pivot_in_either_shape(shape, how):
    from tools.metabase.api import _card_differs

    wanted = _wanted()
    assert _card_differs(_stored(wanted, shape), _mutate(wanted, how))


def test_a_stored_native_card_differs_from_a_wanted_pivot_and_the_other_way_round():
    from tools.metabase.api import _card_body, _card_differs

    wanted = _wanted()
    native = _card_body(PIVOT_DEFS[0]["cards"][0], 1, 2)
    native_stored = {"display": "table", "visualization_settings": {}, "dataset_query": native["dataset_query"]}
    assert _card_differs(native_stored, wanted)
    assert _card_differs(_stored(wanted, "stages"), native)


@pytest.fixture(params=[FakeMetabase, V065Metabase, EchoingMetabase], ids=["legacy", "v065", "echoing"])
def pivot_fake(request):
    server = request.param()
    yield server
    server.server.shutdown()


def test_a_pivot_is_created_over_its_source_and_stored_with_the_built_column_split(pivot_fake):
    counts = provision(Client(pivot_fake.url), ENV, PIVOT_DEFS)
    assert (counts.created, counts.updated) == (6, 0)  # db, collection, 3 cards, 1 dashboard
    rows, pivot = _pivot_card(pivot_fake, "rows card"), _pivot_card(pivot_fake)
    stored = pivot["dataset_query"]
    if "stages" in stored:
        assert "type" not in stored and stored["stages"][0]["source-card"] == rows["id"]
    else:
        assert stored["query"]["source-table"] == f"card__{rows['id']}"
    settings = pivot["visualization_settings"]
    assert settings["pivot_table.column_split"]["columns"] == ["Currency"]
    assert "Currency" not in settings["pivot_table.column_split"]["rows"]
    assert settings["pivot.show_row_totals"] is False  # no total crosses currencies


def test_an_off_dashboard_card_is_upserted_has_no_dashcard_and_the_ids_count_only_dashboard_cards(pivot_fake):
    provision(Client(pivot_fake.url), ENV, PIVOT_DEFS)
    dash = _dash(pivot_fake, "Dash P")
    ids = {c["name"]: c["id"] for c in pivot_fake.cards}
    assert [d["card_id"] for d in dash["dashcards"]] == [ids["pivot card"], ids["plain"]]
    assert [d["id"] for d in dash["dashcards"]] == [-1, -2]
    assert [(d["row"], d["size_y"]) for d in dash["dashcards"]] == [(0, 8), (8, 6)]


def test_an_off_dashboard_card_is_kept_by_the_archive_pass(pivot_fake):
    provision(Client(pivot_fake.url), ENV, PIVOT_DEFS)
    counts = provision(Client(pivot_fake.url), ENV, PIVOT_DEFS)
    assert not _archive_puts(pivot_fake) and counts.archived == 0
    assert not _pivot_card(pivot_fake, "rows card").get("archived")


def test_second_provision_of_a_pivot_dashboard_reports_zero_created_and_zero_updated(pivot_fake):
    provision(Client(pivot_fake.url), ENV, PIVOT_DEFS)
    before = len(pivot_fake.writes())
    counts = provision(Client(pivot_fake.url), ENV, PIVOT_DEFS)
    assert (counts.created, counts.updated, counts.archived, len(pivot_fake.writes())) == (0, 0, 0, before)


def test_a_changed_pivot_value_is_exactly_one_update_of_the_pivot_card(pivot_fake):
    provision(Client(pivot_fake.url), ENV, PIVOT_DEFS)
    counts = provision(Client(pivot_fake.url), ENV, _pivot_defs(values=["Actual", "Plan", "Transactions"]))
    assert [e for e in counts.log if e.endswith("updated")] == ["card pivot card: updated"]


def test_a_changed_pivot_setting_is_exactly_one_update_of_the_pivot_card(pivot_fake):
    provision(Client(pivot_fake.url), ENV, PIVOT_DEFS)
    changed = json.loads(json.dumps(PIVOT_DEFS))
    changed[0]["cards"][1]["visualization_settings"]["column_settings"][SPLIT_KEYS[2]]["column_title"] = "Count"
    counts = provision(Client(pivot_fake.url), ENV, changed)
    assert [e for e in counts.log if e.endswith("updated")] == ["card pivot card: updated"]


def test_a_native_card_is_turned_into_a_pivot_by_one_put():
    """The live case: the old native `This cycle` is PUT over with the pivot body (T5 step 1 (d))."""
    fake = V065Metabase()
    try:
        old = [{"name": "Dash P", "parameters": [], "cards": [
            {"name": "pivot card", "sql": ROWS_SQL, "display": "table", "visualization_settings": {}, "row": 0, "col": 0, "size_x": 24, "size_y": 8}]}]
        provision(Client(fake.url), ENV, old)
        native_id = _pivot_card(fake)["id"]
        counts = provision(Client(fake.url), ENV, PIVOT_DEFS)
        assert "card pivot card: updated" in counts.log and "card rows card: created" in counts.log
        assert _pivot_card(fake)["id"] == native_id and _pivot_card(fake)["display"] == "pivot"
        assert "stages" in _pivot_card(fake)["dataset_query"] and "source-card" in _pivot_card(fake)["dataset_query"]["stages"][0]
    finally:
        fake.server.shutdown()


class StaleMetadataMetabase(FakeMetabase):
    """result_metadata is frozen at the first read of a card: a later PUT of its SQL does not refresh it."""

    def metadata(self, card):
        frozen = self.__dict__.setdefault("frozen", {})
        return frozen.setdefault(card["id"], super().metadata(card))


def test_a_source_card_updated_in_this_run_whose_metadata_lacks_a_pivot_name_raises_with_the_card_name():
    from tools.metabase.api import ApiError  # noqa: F401

    fake = StaleMetadataMetabase()
    try:
        provision(Client(fake.url), ENV, PIVOT_DEFS)
        renamed = json.loads(json.dumps(PIVOT_DEFS))
        renamed[0]["cards"][0]["sql"] = ROWS_SQL.replace('as "Actual"', 'as "Spent"')
        renamed[0]["cards"][1]["pivot"]["values"] = ["Plan", "Spent", "Transactions"]
        with pytest.raises(ValueError) as caught:
            provision(Client(fake.url), ENV, renamed)
        assert "pivot card" in str(caught.value) and "Spent" in str(caught.value)
    finally:
        fake.server.shutdown()


class EmptyUntilRunMetabase(FakeMetabase):
    """result_metadata is empty until the card has been run once."""

    def route(self, method, path, body):
        if path.startswith("/api/card/") and path.endswith("/query") and method == "POST":
            self.__dict__.setdefault("ran", set()).add(int(path.split("/")[3]))
        return super().route(method, path, body)

    def metadata(self, card):
        return super().metadata(card) if card["id"] in self.__dict__.get("ran", set()) else []


def test_an_empty_result_metadata_makes_provision_run_the_source_once_and_read_again():
    fake = EmptyUntilRunMetabase()
    try:
        provision(Client(fake.url), ENV, PIVOT_DEFS)
        runs = [p for m, p, _ in fake.requests if m == "POST" and p.endswith("/query")]
        assert len(runs) == 1 and runs[0] == f"/api/card/{_pivot_card(fake, 'rows card')['id']}/query"
        assert _pivot_card(fake)["dataset_query"]["query"]["source-table"].startswith("card__")
    finally:
        fake.server.shutdown()


def test_a_source_with_metadata_is_never_run(fake):
    provision(Client(fake.url), ENV, PIVOT_DEFS)
    assert not [p for m, p, _ in fake.requests if m == "POST" and p.endswith("/query")]
