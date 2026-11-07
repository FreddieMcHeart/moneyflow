"""The README and AGENTS.md must keep saying the things the local environment depends on."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
README = (ROOT / "README.md").read_text()
AGENTS = (ROOT / "AGENTS.md").read_text()


def test_readme_documents_the_local_environment() -> None:
    for needle in ("## Local environment", "./local.sh up", "./local.sh stop", "./local.sh status",
                   "./local.sh refresh", "MF_OLD_REPO", "MB_ADMIN_EMAIL", "deploy/metabase/dashboards",
                   "overwritten", ".local/", "127.0.0.1", "podman only", "DOCKER_HOST", "ssh -L",
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


def test_readme_pins_metabase_heap_and_oom_check() -> None:
    memory = next(line for line in README.splitlines() if line.startswith("- **Memory.**"))
    assert "-Xmx512m" in memory
    assert "deploy/compose.yml" in memory
    assert "about 2 GB" not in README
    assert "podman machine ssh" in README
    assert "dmesg | grep -i 'killed process'" in README


def test_readme_says_mf_old_repo_is_required_in_env() -> None:
    line = next(line for line in README.splitlines() if "`MF_OLD_REPO`" in line and ".env" in line)
    assert "local.sh refuses" in line


def test_mcp_line_uses_the_read_only_role() -> None:
    line = next(line for line in README.splitlines() if "claude mcp add moneyflow" in line)
    assert "postgresql://mf_reader:<MF_READER_PASSWORD>@localhost:5434/moneyflow_dev" in line
    assert "POSTGRES_PASSWORD" not in line


def test_agents_does_not_use_the_admin_role_for_mcp() -> None:
    for line in AGENTS.splitlines():
        if "claude mcp add" in line:
            assert "POSTGRES_PASSWORD" not in line
