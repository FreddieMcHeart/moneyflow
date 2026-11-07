"""The README and AGENTS.md must keep saying the things the local environment depends on."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
README = (ROOT / "README.md").read_text()
AGENTS = (ROOT / "AGENTS.md").read_text()


def test_readme_documents_the_local_environment() -> None:
    for needle in ("## Local environment", "./local.sh up", "./local.sh stop", "./local.sh status",
                   "./local.sh refresh", "MF_OLD_REPO", "MB_ADMIN_EMAIL", "deploy/metabase/dashboards",
                   "overwritten", ".local/", "127.0.0.1", "podman only", "about 2 GB", "DOCKER_HOST", "ssh -L",
                   "/run/podman/podman.sock", "change-me"):
        assert needle in README, needle


def test_readme_says_which_postgres_is_which() -> None:
    assert "## Which Postgres" in README
    section = README.split("## Which Postgres", 1)[1].split("\n## ", 1)[0]
    for needle in ("mf-pg", "5433", "moneyflow_test", "5434", "moneyflow_dev"):
        assert needle in section, needle


def test_readme_states_metabase_licence_as_fact_about_the_image() -> None:
    assert "AGPL" in README
    assert "not shipped" in README


def test_readme_has_claude_code_mcp_line() -> None:
    assert "claude mcp add moneyflow" in README
    assert "uv run --directory" in README


def test_agents_lists_new_layout_and_test_db_rule() -> None:
    for needle in ("tools/local", "tools/metabase", "deploy/", "5433", "5434"):
        assert needle in AGENTS, needle
    assert "Tests never use `moneyflow_dev`" in AGENTS
