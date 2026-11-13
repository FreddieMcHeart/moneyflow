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
    log: list[str] = field(default_factory=list)

    def record(self, kind: str, name: str, result: str) -> None:
        setattr(self, result, getattr(self, result) + 1)
        self.log.append(f"{kind} {name}: {result}")

    def line(self) -> str:
        return f"created {self.created}, updated {self.updated}, unchanged {self.unchanged}"


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


def _card_body(card: dict, database_id: int, collection_id: int, parameters: list[dict] | None = None) -> dict:
    native = {"query": card["sql"], "template-tags": _template_tags(card["sql"], parameters)}
    return {
        "name": card["name"],
        "collection_id": collection_id,
        "display": card["display"],
        "dataset_query": {"type": "native", "database": database_id, "native": native},
        "visualization_settings": card.get("visualization_settings", {}),
    }


def _tag_view(tags: dict | None) -> dict:
    """Only what we set and Metabase keeps as is: it rewrites ids and display names and adds keys.

    Legacy cards store a dict keyed by name; v0.65 stores a list of tag objects, and no key when there are none."""
    if isinstance(tags, list):
        return {tag["name"]: tag.get("type") for tag in tags}
    return {name: tag.get("type") for name, tag in (tags or {}).items()}


def _card_differs(existing: dict, wanted: dict) -> bool:
    query = existing.get("dataset_query") or {}
    if "stages" in query:  # v0.65 MBQL-lib shape: the stage holds the SQL string and the tags
        stage = (query["stages"] or [{}])[0]
        native = {"query": stage.get("native"), "template-tags": stage.get("template-tags")}
    else:
        native = query.get("native") or {}
    want_native = wanted["dataset_query"]["native"]
    stored_viz = existing.get("visualization_settings") or {}
    return (native.get("query") != want_native["query"]
            or _tag_view(native.get("template-tags")) != _tag_view(want_native["template-tags"])
            or existing.get("display") != wanted["display"]
            or any(stored_viz.get(k) != v for k, v in wanted["visualization_settings"].items()))


def _upsert_card(client: Client, card: dict, database_id: int, collection_id: int, existing: dict[str, dict], counts: Counts,
                 parameters: list[dict] | None = None) -> int:
    wanted = _card_body(card, database_id, collection_id, parameters)
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


def _upsert_dashboard(client: Client, definition: dict, card_ids: list[int], collection_id: int,
                      existing: dict[str, dict], counts: Counts) -> None:
    parameters = _dashboard_parameters(definition)
    dashcards = []
    for i, (card, card_id) in enumerate(zip(definition["cards"], card_ids)):
        used = set(TAG_VAR.findall(card["sql"]))
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
        used = {tag for card in definition["cards"] for tag in TAG_VAR.findall(card["sql"])}
        for tag in sorted(used - set(declared)):
            raise ValueError(f"{path.name}: a card uses {{{{{tag}}}}} but parameters does not declare it")
        for name in sorted(set(declared) - used):
            raise ValueError(f"{path.name}: parameter {name} is declared but no card uses it")
        definitions.append(definition)
    return definitions


def provision(client: Client, env: dict[str, str], definitions: list[dict]) -> Counts:
    counts = Counts()
    authenticate(client, env["MB_ADMIN_EMAIL"], env["MB_ADMIN_PASSWORD"])
    database_id = _ensure_database(client, env, counts)
    collection_id = _ensure_collection(client, counts)
    cards = {c["name"]: c for c in _items(client.get("/api/card")) if c.get("collection_id") == collection_id}
    dashboards = {d["name"]: d for d in _items(client.get("/api/dashboard")) if d.get("collection_id") == collection_id}
    for definition in definitions:
        card_ids = [_upsert_card(client, card, database_id, collection_id, cards, counts, definition.get("parameters")) for card in definition["cards"]]
        _upsert_dashboard(client, definition, card_ids, collection_id, dashboards, counts)
    return counts
