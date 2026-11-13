"""Fake runner only: no container is started, no database is touched. Synthetic values only."""

import pytest

from tools import local
from tools.local import Refused, detect_compose, ensure_env, local_url, read_env

EXAMPLE = (local.ROOT / ".env.example").read_text()


class Runner:
    def __init__(self, fail_at=None, compose_ok=True):
        self.calls, self.fail_at, self.compose_ok = [], fail_at, compose_ok

    def __call__(self, cmd, env=None):
        self.calls.append((list(cmd), env))
        if cmd[-2:] == ["compose", "version"]:
            return 0 if self.compose_ok else 1
        if self.fail_at is not None and len([c for c in self.calls if c[0][-2:] != ["compose", "version"]]) == self.fail_at:
            return 7
        return 0


def _root(tmp_path, old_repo=True):
    (tmp_path / ".env.example").write_text(EXAMPLE)
    root = tmp_path
    if old_repo:
        old = tmp_path / "old"
        old.mkdir()
        ensure_env(root)
        text = (root / ".env").read_text().replace("/path/to/old/repo", str(old))
        (root / ".env").write_text(text)
    return root


def _which(*present):
    return lambda name: f"/bin/{name}" if name in present else None


def _kinds(runner):
    out = []
    for cmd, _ in runner.calls:
        if cmd[-2:] == ["compose", "version"]:
            continue
        joined = " ".join(cmd)
        out.append("compose" if "compose" in cmd else "alembic" if "alembic" in joined else "import" if "tools.import_old" in joined else "metabase" if "tools.metabase" in joined else "rebuild")
    return out


def test_up_runs_steps_in_order(tmp_path):
    r, lines = Runner(), []
    assert local.up(_root(tmp_path), r, which=_which("docker"), out=lines.append) == 0
    assert _kinds(r) == ["compose", "alembic", "import", "rebuild", "metabase"]
    assert "docker" == r.calls[0][0][0]


@pytest.mark.parametrize("fail_at,expected", [(1, ["compose"]), (2, ["compose", "alembic"]), (3, ["compose", "alembic", "import"])])
def test_up_stops_at_first_non_zero(tmp_path, fail_at, expected):
    r = Runner(fail_at=fail_at)
    assert local.up(_root(tmp_path), r, which=_which("docker"), out=lambda s: None) == 7
    assert _kinds(r) == expected


def test_up_without_old_repo_refuses_before_any_container_call(tmp_path):
    r, lines = Runner(), []
    root = _root(tmp_path, old_repo=False)
    assert local.up(root, r, which=_which("docker"), out=lines.append) == 2
    assert _kinds(r) == []
    assert any("MF_OLD_REPO" in line for line in lines)


def test_existing_env_is_not_rewritten(tmp_path):
    (tmp_path / ".env.example").write_text(EXAMPLE)
    (tmp_path / ".env").write_text("POSTGRES_PASSWORD=mine\n")
    assert ensure_env(tmp_path) is False
    assert (tmp_path / ".env").read_text() == "POSTGRES_PASSWORD=mine\n"


def test_generated_passwords_differ_across_fresh_envs(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    for d in (a, b):
        d.mkdir()
        (d / ".env.example").write_text(EXAMPLE)
        assert ensure_env(d) is True
    ea, eb = read_env(a / ".env"), read_env(b / ".env")
    for key in local.GENERATED:
        assert ea[key] != eb[key] and ea[key] != "change-me"
    assert ea["MF_OLD_REPO"] == local.PLACEHOLDER_OLD_REPO


def test_nothing_printed_contains_a_password(tmp_path, capsys):
    root = _root(tmp_path)
    lines = []
    local.up(root, Runner(), which=_which("docker"), out=lines.append)
    local.status(root, Runner(), which=_which("docker"),
                 query=lambda url: {"counts": {"t": 1}, "revision": "r", "built_for": None},
                 health=lambda p: "ok", out=lines.append)
    printed = "\n".join(lines) + capsys.readouterr().out
    env = read_env(root / ".env")
    for key in local.GENERATED:
        assert env[key] not in printed
    assert "***" in printed


def test_local_url_masks():
    env = {"POSTGRES_PASSWORD": "s3cr3t", "MF_PG_PORT": "5434"}
    assert "s3cr3t" not in local_url(env, mask=True)
    assert local_url(env).endswith("@localhost:5434/moneyflow_dev")


def test_detect_prefers_docker_then_podman():
    assert detect_compose(Runner(), _which("docker", "podman")) == ["docker", "compose"]
    assert detect_compose(Runner(), _which("podman")) == ["podman", "compose"]


def test_detect_skips_binary_without_compose_plugin():
    class R(Runner):
        def __call__(self, cmd, env=None):
            self.calls.append((list(cmd), env))
            return 0 if cmd[0] == "podman" else 1

    assert detect_compose(R(), _which("docker", "podman")) == ["podman", "compose"]


def test_detect_honours_override_and_refuses_when_none():
    assert detect_compose(Runner(), _which(), "podman compose") == ["podman", "compose"]
    with pytest.raises(Refused, match="neither"):
        detect_compose(Runner(), _which())


def test_up_with_no_runtime_refuses_before_any_call(tmp_path):
    r, lines = Runner(), []
    assert local.up(_root(tmp_path), r, which=_which(), out=lines.append) == 2
    assert r.calls == [] and "neither" in lines[0]


def test_podman_is_used_for_compose(tmp_path):
    r = Runner()
    local.up(_root(tmp_path), r, which=_which("podman"), out=lambda s: None)
    compose = [c for c, _ in r.calls if "-f" in c]
    assert compose[0][:2] == ["podman", "compose"]


def test_refresh_and_stop(tmp_path):
    r = Runner()
    assert local.refresh(_root(tmp_path), r, out=lambda s: None) == 0
    assert _kinds(r) == ["import", "rebuild"]
    r2 = Runner()
    local.stop(tmp_path, r2, which=_which("docker"))
    assert r2.calls[-1][0][-1] == "stop"


def test_password_generator_retries_until_digit_lower_and_upper(monkeypatch):
    feed = iter(["NoDigitsHereAtAll", "alllower1234", "ALLUPPER1234", "Good1Value"])
    monkeypatch.setattr(local.secrets, "token_urlsafe", lambda n: next(feed))
    assert local.new_password() == "Good1Value"


def test_generated_env_passwords_all_have_digit_lower_and_upper(tmp_path):
    for i in range(30):
        d = tmp_path / str(i)
        d.mkdir()
        (d / ".env.example").write_text(EXAMPLE)
        ensure_env(d)
        for key in local.GENERATED:
            value = read_env(d / ".env")[key]
            assert any(c.isdigit() for c in value) and value.lower() != value and value.upper() != value


def test_up_creates_bind_dirs_before_the_compose_call(tmp_path):
    root = _root(tmp_path)
    seen = []

    class Checking(Runner):
        def __call__(self, cmd, env=None):
            if "up" in cmd and "compose" in cmd:
                seen.extend((root / rel).is_dir() for rel in local.BIND_DIRS)
            return super().__call__(cmd, env)

    assert local.up(root, Checking(), which=_which("docker"), out=lambda s: None) == 0
    assert seen and all(seen)


def test_bind_dirs_cover_every_local_bind_mount_in_compose():
    import yaml

    compose = yaml.safe_load((local.ROOT / local.COMPOSE_FILE).read_text())
    mounts = {v.split(":", 1)[0] for s in compose["services"].values() for v in s.get("volumes", []) if v.startswith("../.local/")}
    assert mounts == {"../" + rel for rel in local.BIND_DIRS}


@pytest.mark.parametrize("value", ["", "change-me"])
def test_up_refuses_empty_or_placeholder_password_naming_only_the_key(tmp_path, value):
    root = _root(tmp_path)
    text = (root / ".env").read_text().replace(read_env(root / ".env")["MB_ADMIN_PASSWORD"], value)
    (root / ".env").write_text(text)
    r, lines = Runner(), []
    assert local.up(root, r, which=_which("docker"), out=lines.append) == 2
    assert _kinds(r) == []
    assert any("MB_ADMIN_PASSWORD" in line for line in lines)
    assert not any("change-me" in line for line in lines)


def test_up_passes_no_password_or_url_in_any_argv(tmp_path):
    root = _root(tmp_path)
    password = read_env(root / ".env")["POSTGRES_PASSWORD"]
    r = Runner()
    assert local.up(root, r, which=_which("docker"), out=lambda s: None) == 0
    for cmd, _ in r.calls:
        joined = " ".join(cmd)
        assert password not in joined and "postgresql://" not in joined


def test_up_hands_the_url_to_import_and_rebuild_through_the_environment(tmp_path):
    root = _root(tmp_path)
    r = Runner()
    assert local.up(root, r, which=_which("docker"), out=lambda s: None) == 0
    url = local_url(read_env(root / ".env"))
    by_kind = dict(zip(_kinds(r), [env for (cmd, env) in r.calls if cmd[-2:] != ["compose", "version"]], strict=True))
    assert by_kind["import"] == {"MONEYFLOW_DATABASE_URL": url}
    assert by_kind["rebuild"] == {"MONEYFLOW_DATABASE_URL": url}
