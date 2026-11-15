"""Metabase provisioner: a tiny REST client (urllib only) and an idempotent upsert-by-name."""

import json
import re
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from pathlib import Path

COLLECTION_NAME = "MoneyFlow (provisioned)"
DATABASE_NAME = "moneyflow"
READER_ROLE = "mf_reader"  # N5: the data connection is read-only, never the owner role


class ApiError(Exception):
    """Method, path, status and Metabase's own error text (truncated). Never the request body."""


def _error_text(error: urllib.error.HTTPError) -> str:
    try:
        payload = json.loads(error.read())
    except (ValueError, OSError):
        return ""
    detail = payload.get("errors") or payload.get("message") if isinstance(payload, dict) else None
    if not detail:
        return ""
    text = detail if isinstance(detail, str) else json.dumps(detail)
    return ": " + text[:200]


class Client:
    def __init__(self, base_url: str, timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.token: str | None = None

    def request(self, method: str, path: str, body: dict | None = None):
        data = None if body is None else json.dumps(body).encode()
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["X-Metabase-Session"] = self.token
        req = urllib.request.Request(self.base_url + path, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as error:
            raise ApiError(f"{method} {path} -> HTTP {error.code}{_error_text(error)}") from None
        except urllib.error.URLError as error:
            raise ApiError(f"{method} {path} -> unreachable ({type(error.reason).__name__})") from None
        return json.loads(raw) if raw else None

    def get(self, path):
        return self.request("GET", path)

    def post(self, path, body):
        return self.request("POST", path, body)

    def put(self, path, body):
        return self.request("PUT", path, body)


@dataclass
class Counts:
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    archived: int = 0
    log: list[str] = field(default_factory=list)

    def record(self, kind: str, name: str, result: str) -> None:
        setattr(self, result, getattr(self, result) + 1)
        self.log.append(f"{kind} {name}: {result}")

    def line(self) -> str:
        return f"created {self.created}, updated {self.updated}, unchanged {self.unchanged}, archived {self.archived}"


def authenticate(client: Client, email: str, password: str) -> None:
    token = (client.get("/api/session/properties") or {}).get("setup-token")
    if token:
        body = {
            "token": token,
            "user": {"first_name": "MoneyFlow", "last_name": "Admin", "email": email, "password": password,
                     "site_name": "MoneyFlow"},
            "prefs": {"site_name": "MoneyFlow", "allow_tracking": False},
        }
        session = client.post("/api/setup", body)
    else:
        session = client.post("/api/session", {"username": email, "password": password})
    client.token = session["id"]


def _items(payload) -> list[dict]:
    return payload["data"] if isinstance(payload, dict) else payload


def _ensure_database(client: Client, env: dict[str, str], counts: Counts) -> int:
    for db in _items(client.get("/api/database")):
        if db["name"] == DATABASE_NAME:
            counts.record("database", DATABASE_NAME, "unchanged")
            return db["id"]
    created = client.post("/api/database", {
        "name": DATABASE_NAME,
        "engine": "postgres",
        "details": {"host": "postgres", "port": 5432, "dbname": "moneyflow_dev", "user": READER_ROLE,
                    "password": env["MF_READER_PASSWORD"], "ssl": False},
    })
    counts.record("database", DATABASE_NAME, "created")
    return created["id"]


def _ensure_collection(client: Client, counts: Counts) -> int:
    for collection in _items(client.get("/api/collection")):
        if collection["name"] == COLLECTION_NAME and not collection.get("archived"):
            counts.record("collection", COLLECTION_NAME, "unchanged")
            return collection["id"]
    created = client.post("/api/collection", {"name": COLLECTION_NAME})
    counts.record("collection", COLLECTION_NAME, "created")
    return created["id"]


TAG_VAR = re.compile(r"\{\{\s*(\w+)\s*\}\}")


def _template_tags(sql: str, parameters: list[dict] | None) -> dict[str, dict]:
    types = {p["name"]: p.get("type", "text") for p in parameters or []}
    return {
        name: {"id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"moneyflow/{name}")), "name": name,
               "display-name": name.replace("_", " ").title(), "type": types.get(name, "text")}
        for name in dict.fromkeys(TAG_VAR.findall(sql))
    }


def _pivot_body(card: dict, database_id: int, source: tuple[int, list[dict] | None] | None) -> tuple[dict, dict]:
    """The query-builder query of a pivot card, and its visualization settings with `pivot_table.column_split` built.

    `source` is (source card id, its result_metadata); base types come from the metadata, never guessed."""
    pivot = card["pivot"]
    if source is None or not source[1]:
        raise ValueError(f"card {card['name']}: pivot source {pivot['source']} has no result_metadata")
    source_id, metadata = source
    types = {column["name"]: column["base_type"] for column in metadata}
    names = [*pivot["rows"], *pivot["columns"], *pivot["values"]]
    missing = [name for name in names if name not in types]
    if missing:
        raise ValueError(f"card {card['name']}: pivot source {pivot['source']} has no column {', '.join(missing)} in its result_metadata")

    def field(name: str) -> list:
        return ["field", name, {"base-type": types[name]}]

    query = {"source-table": f"card__{source_id}",
             "breakout": [field(name) for name in [*pivot["rows"], *pivot["columns"]]],
             "aggregation": [["sum", field(name)] for name in pivot["values"]]}
    values = ["sum"] + [f"sum_{i}" for i in range(2, len(pivot["values"]) + 1)]
    settings = {**card.get("visualization_settings", {}),
                "pivot_table.column_split": {"rows": list(pivot["rows"]), "columns": list(pivot["columns"]), "values": values}}
    return {"type": "query", "database": database_id, "query": query}, settings


def _card_body(card: dict, database_id: int, collection_id: int, parameters: list[dict] | None = None,
               source: tuple[int, list[dict] | None] | None = None) -> dict:
    """Pure: never calls the client. A pivot card needs `source` = (source card id, result_metadata)."""
    if "pivot" in card:
        dataset_query, settings = _pivot_body(card, database_id, source)
    else:
        native = {"query": card["sql"], "template-tags": _template_tags(card["sql"], parameters)}
        dataset_query, settings = {"type": "native", "database": database_id, "native": native}, card.get("visualization_settings", {})
    return {
        "name": card["name"],
        "collection_id": collection_id,
        "display": card["display"],
        "dataset_query": dataset_query,
        "visualization_settings": settings,
    }


def _tag_view(tags: dict | None) -> dict:
    """Only what we set and Metabase keeps as is: it rewrites ids and display names and adds keys.

    Legacy cards store a dict keyed by name; v0.65 stores a list of tag objects, and no key when there are none."""
    if isinstance(tags, list):
        return {tag["name"]: tag.get("type") for tag in tags}
    return {name: tag.get("type") for name, tag in (tags or {}).items()}


def _source_id(value) -> int | None:
    """`card__12` (legacy source-table) or 12 (v0.65 source-card) as 12."""
    return int(str(value).removeprefix("card__")) if value is not None else None


def _query_view(dataset_query: dict | None) -> tuple:
    """What we set in a query, in a form both stored shapes reduce to; lib/uuid and the like are ignored.

    Native: ("native", sql, tag types). Query builder: ("query", source id, breakout names, aggregation (op, name))."""
    query = dataset_query or {}
    if "stages" in query:  # v0.65 MBQL-lib shape: no top-level type, the field name is the LAST element of a field ref
        stage = (query["stages"] or [{}])[0]
        if "source-card" in stage or "source-table" in stage:
            return ("query", _source_id(stage.get("source-card", stage.get("source-table"))),
                    tuple(ref[-1] for ref in stage.get("breakout") or []),
                    tuple((agg[0], agg[-1][-1]) for agg in stage.get("aggregation") or []))
        return ("native", stage.get("native"), _tag_view(stage.get("template-tags")))
    if "query" in query and "native" not in query:  # legacy: ["field", name, {...}]
        legacy = query["query"]
        return ("query", _source_id(legacy.get("source-table")),
                tuple(ref[1] for ref in legacy.get("breakout") or []),
                tuple((agg[0], agg[1][1]) for agg in legacy.get("aggregation") or []))
    native = query.get("native") or {}
    return ("native", native.get("query"), _tag_view(native.get("template-tags")))


def _card_differs(existing: dict, wanted: dict) -> bool:
    stored_viz = existing.get("visualization_settings") or {}
    return (_query_view(existing.get("dataset_query")) != _query_view(wanted["dataset_query"])
            or existing.get("display") != wanted["display"]
            or any(stored_viz.get(k) != v for k, v in wanted["visualization_settings"].items()))


def _upsert_card(client: Client, card: dict, database_id: int, collection_id: int, existing: dict[str, dict], counts: Counts,
                 parameters: list[dict] | None = None, source: tuple[int, list[dict] | None] | None = None) -> int:
    wanted = _card_body(card, database_id, collection_id, parameters, source)
    current = existing.get(card["name"])
    if current is None:
        created = client.post("/api/card", wanted)
        counts.record("card", card["name"], "created")
        return created["id"]
    if _card_differs(current, wanted):
        client.put(f"/api/card/{current['id']}", wanted)
        counts.record("card", card["name"], "updated")
    else:
        counts.record("card", card["name"], "unchanged")
    return current["id"]


PARAMETER_TYPES = {"text": "string/=", "number": "number/="}


def _layout(dashcards: list[dict]) -> list[tuple]:
    return sorted((d["card_id"], d["row"], d["col"], d["size_x"], d["size_y"]) for d in dashcards)


def _dashboard_parameters(definition: dict) -> list[dict]:
    return [{"id": uuid.uuid5(uuid.NAMESPACE_URL, f"moneyflow/{definition['name']}/{p['name']}").hex[:8],
             "name": p["name"].replace("_", " ").capitalize(), "slug": p["name"],
             "type": PARAMETER_TYPES[p.get("type", "text")]}
            for p in definition.get("parameters") or []]


def _parameter_view(parameters: list[dict] | None) -> list[tuple]:
    """Only what we set: Metabase adds keys such as sectionId."""
    return sorted((p["id"], p["slug"], p["type"]) for p in parameters or [])


def _mapping_view(dashcards: list[dict]) -> list[tuple]:
    return sorted((d["card_id"], m["parameter_id"], json.dumps(m["target"]))
                  for d in dashcards for m in d.get("parameter_mappings") or [])


def _on_dashboard(card: dict) -> bool:
    return card.get("on_dashboard", True)


def _upsert_dashboard(client: Client, definition: dict, card_ids: list[int], collection_id: int,
                      existing: dict[str, dict], counts: Counts) -> None:
    parameters = _dashboard_parameters(definition)
    dashcards = []
    on_dashboard = [(card, card_id) for card, card_id in zip(definition["cards"], card_ids) if _on_dashboard(card)]
    for i, (card, card_id) in enumerate(on_dashboard):
        used = set(TAG_VAR.findall(card.get("sql", "")))
        mappings = [{"parameter_id": p["id"], "card_id": card_id, "target": ["variable", ["template-tag", p["slug"]]]}
                    for p in parameters if p["slug"] in used]
        dashcards.append({"id": -(i + 1), "card_id": card_id, "row": card["row"], "col": card["col"],
                          "size_x": card["size_x"], "size_y": card["size_y"], "visualization_settings": {},
                          "parameter_mappings": mappings})
    body = {"dashcards": dashcards, "parameters": parameters}
    current = existing.get(definition["name"])
    if current is None:
        created = client.post("/api/dashboard", {"name": definition["name"], "collection_id": collection_id})
        client.put(f"/api/dashboard/{created['id']}", body)
        counts.record("dashboard", definition["name"], "created")
        return
    stored = client.get(f"/api/dashboard/{current['id']}")
    stored_cards = stored.get("dashcards") or []
    if (_layout(stored_cards) != _layout(dashcards)
            or _parameter_view(stored.get("parameters")) != _parameter_view(parameters)
            or _mapping_view(stored_cards) != _mapping_view(dashcards)):
        client.put(f"/api/dashboard/{current['id']}", body)
        counts.record("dashboard", definition["name"], "updated")
    else:
        counts.record("dashboard", definition["name"], "unchanged")


def _check_pivot_cards(file: str, definition: dict) -> None:
    sql_seen: set[str] = set()
    for card in definition["cards"]:
        if ("sql" in card) == ("pivot" in card):
            raise ValueError(f"{file}: card {card.get('name')} needs exactly one of sql or pivot")
        if "pivot" in card:
            if definition.get("parameters"):
                raise ValueError(f"{file}: card {card['name']} is a pivot on a dashboard with parameters, which is not built")
            if card["pivot"].get("source") not in sql_seen:
                raise ValueError(f"{file}: card {card['name']} has a pivot source that is not an earlier sql card in the file")
            if "pivot_table.column_split" in (card.get("visualization_settings") or {}):
                raise ValueError(f"{file}: card {card['name']} writes pivot_table.column_split, the provisioner builds it")
        else:
            sql_seen.add(card["name"])


def load_definitions(directory: Path) -> list[dict]:
    definitions = []
    for path in sorted(directory.glob("*.json")) if directory.is_dir() else []:
        definition = json.loads(path.read_text())
        if "name" not in definition or "cards" not in definition:
            raise ValueError(f"{path.name}: needs name and cards")
        declared = {p["name"]: p.get("type", "text") for p in definition.get("parameters") or []}
        for name, kind in declared.items():
            if kind not in PARAMETER_TYPES:
                raise ValueError(f"{path.name}: parameter {name} has unknown type {kind}, use one of {sorted(PARAMETER_TYPES)}")
        _check_pivot_cards(path.name, definition)
        used = {tag for card in definition["cards"] if "sql" in card for tag in TAG_VAR.findall(card["sql"])}
        for tag in sorted(used - set(declared)):
            raise ValueError(f"{path.name}: a card uses {{{{{tag}}}}} but parameters does not declare it")
        for name in sorted(set(declared) - used):
            raise ValueError(f"{path.name}: parameter {name} is declared but no card uses it")
        definitions.append(definition)
    return definitions


def _archive_stale(client: Client, collection_id: int, card_names: set[str], dashboard_names: set[str], counts: Counts) -> None:
    """L2: the collection is the provisioner's own, so a card or dashboard no definition names is archived."""
    for item in _items(client.get(f"/api/collection/{collection_id}/items")):
        if item["model"] == "card" and item["name"] not in card_names:
            client.put(f"/api/card/{item['id']}", {"archived": True})
            counts.record("card", item["name"], "archived")
        elif item["model"] == "dashboard" and item["name"] not in dashboard_names:
            client.put(f"/api/dashboard/{item['id']}", {"archived": True})
            counts.record("dashboard", item["name"], "archived")


def _result_metadata(client: Client, card_id: int) -> list[dict]:
    """A native card has result_metadata right after it is saved; if it is empty, run the card once and read again."""
    metadata = (client.get(f"/api/card/{card_id}") or {}).get("result_metadata")
    if not metadata:
        client.post(f"/api/card/{card_id}/query", {})
        metadata = (client.get(f"/api/card/{card_id}") or {}).get("result_metadata")
    return metadata or []


def provision(client: Client, env: dict[str, str], definitions: list[dict]) -> Counts:
    counts = Counts()
    authenticate(client, env["MB_ADMIN_EMAIL"], env["MB_ADMIN_PASSWORD"])
    database_id = _ensure_database(client, env, counts)
    collection_id = _ensure_collection(client, counts)
    cards = {c["name"]: c for c in _items(client.get("/api/card")) if c.get("collection_id") == collection_id}
    dashboards = {d["name"]: d for d in _items(client.get("/api/dashboard")) if d.get("collection_id") == collection_id}
    for definition in definitions:
        sources = {card["pivot"]["source"] for card in definition["cards"] if "pivot" in card}
        ids: dict[str, int] = {}
        metadata: dict[str, list[dict]] = {}
        for card in definition["cards"]:
            source = (ids[card["pivot"]["source"]], metadata[card["pivot"]["source"]]) if "pivot" in card else None
            ids[card["name"]] = _upsert_card(client, card, database_id, collection_id, cards, counts, definition.get("parameters"), source)
            if card["name"] in sources:  # re-read after the write: a source updated in this run must not hand over stale metadata
                metadata[card["name"]] = _result_metadata(client, ids[card["name"]])
        _upsert_dashboard(client, definition, [ids[card["name"]] for card in definition["cards"]], collection_id, dashboards, counts)
    card_names = {card["name"] for definition in definitions for card in definition["cards"]}
    _archive_stale(client, collection_id, card_names, {definition["name"] for definition in definitions}, counts)
    return counts
