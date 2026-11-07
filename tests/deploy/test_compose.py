"""deploy/compose.yml: both services, a pinned Metabase tag, data under .local/, no literal secrets."""

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ROOT / "deploy" / "compose.yml"
ENV_EXAMPLE = ROOT / ".env.example"


def _compose() -> dict:
    return yaml.safe_load(COMPOSE.read_text())


def test_both_services_exist_and_project_name_is_fixed():
    compose = _compose()
    assert set(compose["services"]) == {"postgres", "metabase"}
    assert compose["name"] == "moneyflow-local"


def test_images_are_pinned():
    for service in _compose()["services"].values():
        image = service["image"]
        _, _, tag = image.partition(":")
        assert tag and tag != "latest", image
    assert _compose()["services"]["metabase"]["image"].startswith("metabase/metabase:v")


def test_postgres_data_is_a_bind_mount_under_local():
    volumes = _compose()["services"]["postgres"]["volumes"]
    assert "../.local/pg:/var/lib/postgresql/data" in volumes
    assert "volumes" not in _compose(), "no named volumes"


def test_app_database_is_moneyflow_dev_on_port_5434_by_default():
    postgres = _compose()["services"]["postgres"]
    assert postgres["environment"]["POSTGRES_DB"] == "moneyflow_dev"
    assert postgres["ports"] == ["127.0.0.1:${MF_PG_PORT:-5434}:5432"]


def test_no_literal_password_in_compose():
    for line in COMPOSE.read_text().splitlines():
        if re.search(r"PASS", line):
            assert "${" in line, line


def test_env_example_lists_every_variable():
    text = ENV_EXAMPLE.read_text()
    for name in (
        "MF_PG_PORT", "MF_MB_PORT", "POSTGRES_PASSWORD", "MF_READER_PASSWORD",
        "MB_DB_PASSWORD", "MB_ADMIN_EMAIL", "MB_ADMIN_PASSWORD", "MF_OLD_REPO",
    ):
        assert re.search(rf"^{name}=", text, re.M), name


def test_published_ports_are_bound_to_loopback_only():
    for name, service in _compose()["services"].items():
        for port in service.get("ports", []):
            assert port.startswith("127.0.0.1:"), (name, port)


def test_metabase_heap_is_capped():
    env = _compose()["services"]["metabase"]["environment"]
    if isinstance(env, list):
        env = dict(item.split("=", 1) for item in env)
    flags = str(env.get("JAVA_OPTS", "")).split()
    assert flags == [
        "-Xmx384m", "-XX:+UseSerialGC", "-XX:ReservedCodeCacheSize=64m",
        "-Xss512k", "-XX:TieredStopAtLevel=1",
    ]
