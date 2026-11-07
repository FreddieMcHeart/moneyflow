"""Metabase provisioner: a tiny REST client (urllib only) and an idempotent upsert-by-name."""

import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

COLLECTION_NAME = "MoneyFlow (provisioned)"
DATABASE_NAME = "moneyflow"
READER_ROLE = "mf_reader"  # N5: the data connection is read-only, never the owner role


class ApiError(Exception):
    """Carries the method, path and status only: a response body could echo a secret."""


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
            raise ApiError(f"{method} {path} -> HTTP {error.code}") from None
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
            counts.unchanged += 1
            return db["id"]
    created = client.post("/api/database", {
        "name": DATABASE_NAME,
        "engine": "postgres",
        "details": {"host": "postgres", "port": 5432, "dbname": "moneyflow_dev", "user": READER_ROLE,
                    "password": env["MF_READER_PASSWORD"], "ssl": False},
    })
    counts.created += 1
    return created["id"]


def _ensure_collection(client: Client, counts: Counts) -> int:
    for collection in _items(client.get("/api/collection")):
        if collection["name"] == COLLECTION_NAME and not collection.get("archived"):
            counts.unchanged += 1
            return collection["id"]
    created = client.post("/api/collection", {"name": COLLECTION_NAME})
    counts.created += 1
    return created["id"]


def _card_body(card: dict, database_id: int, collection_id: int) -> dict:
    return {
        "name": card["name"],
        "collection_id": collection_id,
        "display": card["display"],
        "dataset_query": {"type": "native", "database": database_id, "native": {"query": card["sql"]}},
        "visualization_settings": card.get("visualization_settings", {}),
    }


def _card_differs(existing: dict, wanted: dict) -> bool:
    native = (existing.get("dataset_query") or {}).get("native") or {}
    return (native.get("query") != wanted["dataset_query"]["native"]["query"]
            or existing.get("display") != wanted["display"]
            or (existing.get("visualization_settings") or {}) != wanted["visualization_settings"])


def _upsert_card(client: Client, card: dict, database_id: int, collection_id: int, existing: dict[str, dict], counts: Counts) -> int:
    wanted = _card_body(card, database_id, collection_id)
    current = existing.get(card["name"])
    if current is None:
        created = client.post("/api/card", wanted)
        counts.created += 1
        return created["id"]
    if _card_differs(current, wanted):
        client.put(f"/api/card/{current['id']}", wanted)
        counts.updated += 1
    else:
        counts.unchanged += 1
    return current["id"]


def _layout(dashcards: list[dict]) -> list[tuple]:
    return sorted((d["card_id"], d["row"], d["col"], d["size_x"], d["size_y"]) for d in dashcards)


def _upsert_dashboard(client: Client, definition: dict, card_ids: list[int], collection_id: int,
                      existing: dict[str, dict], counts: Counts) -> None:
    dashcards = [
        {"id": -(i + 1), "card_id": card_id, "row": card["row"], "col": card["col"],
         "size_x": card["size_x"], "size_y": card["size_y"], "visualization_settings": {}}
        for i, (card, card_id) in enumerate(zip(definition["cards"], card_ids))
    ]
    current = existing.get(definition["name"])
    if current is None:
        created = client.post("/api/dashboard", {"name": definition["name"], "collection_id": collection_id})
        client.put(f"/api/dashboard/{created['id']}", {"dashcards": dashcards})
        counts.created += 1
        return
    stored = client.get(f"/api/dashboard/{current['id']}")
    if _layout(stored.get("dashcards") or []) != _layout(dashcards):
        client.put(f"/api/dashboard/{current['id']}", {"dashcards": dashcards})
        counts.updated += 1
    else:
        counts.unchanged += 1


def load_definitions(directory: Path) -> list[dict]:
    definitions = []
    for path in sorted(directory.glob("*.json")) if directory.is_dir() else []:
        definition = json.loads(path.read_text())
        if "name" not in definition or "cards" not in definition:
            raise ValueError(f"{path.name}: needs name and cards")
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
        card_ids = [_upsert_card(client, card, database_id, collection_id, cards, counts) for card in definition["cards"]]
        _upsert_dashboard(client, definition, card_ids, collection_id, dashboards, counts)
    return counts
