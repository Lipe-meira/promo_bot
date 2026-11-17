"""The Render Blueprint (`render.yaml`) and the separation of the API from the bot.

The Blueprint is reviewable configuration, not an action: nothing is created on Render until the
owner applies it. These tests check the properties that matter for a free, manually deployed,
migration-free instance. The file is read as text on purpose (no YAML dependency for one file).
"""

from __future__ import annotations

import ast
import re
import tomllib
from pathlib import Path

import pytest

from promo_api.settings import ApiSettings

API_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = API_ROOT.parents[1]
BLUEPRINT = REPO_ROOT / "render.yaml"

SECRET_VARIABLES = {
    "PROMO_API_DATABASE_URL",
    "PROMO_API_CLERK_ISSUER",
    "PROMO_API_CLERK_AUTHORIZED_PARTIES",
    "PROMO_API_CLERK_SECRET_KEY",
}


@pytest.fixture(scope="module")
def blueprint() -> str:
    return BLUEPRINT.read_text(encoding="utf-8")


def _env_vars(text: str) -> dict[str, str | None]:
    """`key -> inline value` (None when declared with `sync: false`) of the `envVars` list."""

    found: dict[str, str | None] = {}
    lines = text.splitlines()
    for index, line in enumerate(lines):
        match = re.match(r"\s*-\s*key:\s*(\S+)\s*$", line)
        if not match:
            continue
        value: str | None = None
        for follow in lines[index + 1 : index + 3]:
            inline = re.match(r"\s*value:\s*(.+?)\s*$", follow)
            if inline:
                value = inline.group(1).strip("\"'")
        found[match.group(1)] = value
    return found


def test_a_single_free_python_web_service_rooted_at_the_api(blueprint: str) -> None:
    assert re.search(r"^\s*-?\s*type:\s*web\s*$", blueprint, re.M)
    assert re.search(r"^\s*runtime:\s*python\s*$", blueprint, re.M)
    assert re.search(r"^\s*plan:\s*free\s*$", blueprint, re.M)
    assert re.search(r"^\s*rootDir:\s*apps/api\s*$", blueprint, re.M)
    assert len(re.findall(r"^\s*-?\s*type:", blueprint, re.M)) == 1  # no database, no worker


def test_deploys_are_manual(blueprint: str) -> None:
    assert re.search(r"^\s*autoDeployTrigger:\s*\"?off\"?\s*$", blueprint, re.M)
    assert "autoDeploy: true" not in blueprint


def test_no_migration_runs_during_a_deploy(blueprint: str) -> None:
    # Pre-deploy commands are not available on Free instances, and migrations never run from the
    # start command either: they are a separate, deliberate operator step.
    assert "preDeployCommand" not in blueprint
    assert "alembic" not in blueprint


def test_the_build_installs_exactly_the_locked_runtime_dependencies(blueprint: str) -> None:
    build = re.search(r"^\s*buildCommand:\s*(.+)$", blueprint, re.M)
    assert build is not None
    assert "uv sync --locked --no-dev" in build.group(1)
    assert "--frozen" not in build.group(1)  # --locked fails when the lockfile is out of date


def test_the_server_binds_all_interfaces_on_the_port_render_provides(blueprint: str) -> None:
    start = re.search(r"^\s*startCommand:\s*(.+)$", blueprint, re.M)
    assert start is not None
    command = start.group(1)
    assert "promo_api.main:create --factory" in command
    assert "--host 0.0.0.0" in command
    assert "--port $PORT" in command
    assert "--reload" not in command
    assert "--workers" not in command  # one worker: a free instance has 512 MB


def test_the_health_check_does_not_wake_the_database(blueprint: str) -> None:
    # Render probes the health check path continuously; /readyz queries PostgreSQL, which would
    # keep a free, scale-to-zero database awake and burn its monthly compute hours.
    assert re.search(r"^\s*healthCheckPath:\s*/healthz\s*$", blueprint, re.M)


def test_python_is_pinned_to_the_version_the_api_supports(blueprint: str) -> None:
    pinned = _env_vars(blueprint).get("PYTHON_VERSION")
    assert pinned is not None
    assert re.fullmatch(r"3\.12\.\d+", pinned)


def test_secrets_are_declared_without_values(blueprint: str) -> None:
    env = _env_vars(blueprint)

    for name in SECRET_VARIABLES:
        assert name in env
        assert env[name] is None, f"{name} must be `sync: false`, never an inline value"
    assert len(re.findall(r"sync:\s*false", blueprint)) >= len(SECRET_VARIABLES)


def test_no_secret_shaped_value_is_committed(blueprint: str) -> None:
    for fragment in ("postgresql", "sk_test", "sk_live", "pk_test", "pk_live", "neon.tech", "@"):
        assert fragment not in blueprint


def test_every_variable_names_a_real_setting() -> None:
    fields = {f"PROMO_API_{name.upper()}" for name in ApiSettings.model_fields}
    declared = {name for name in _env_vars(BLUEPRINT.read_text("utf-8")) if name.startswith("PROMO_")}

    assert declared <= fields, f"unknown settings: {sorted(declared - fields)}"


def test_every_required_setting_is_declared() -> None:
    required = {
        f"PROMO_API_{name.upper()}"
        for name, field in ApiSettings.model_fields.items()
        if field.is_required()
    }

    assert required <= set(_env_vars(BLUEPRINT.read_text("utf-8")))


def test_interactive_docs_are_off_on_the_public_instance(blueprint: str) -> None:
    assert _env_vars(blueprint).get("PROMO_API_ENVIRONMENT") == "production"


# --- separation from the bot ---------------------------------------------------------------

BOT_ONLY_PACKAGES = {"telethon", "playwright", "aiogram", "aiosqlite", "promo_bot", "yaml"}


def _imported_roots() -> set[str]:
    roots: set[str] = set()
    for path in (API_ROOT / "src").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                roots.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                roots.add(node.module.split(".")[0])
    return roots


def test_the_api_never_imports_the_bot_or_its_dependencies() -> None:
    assert not _imported_roots() & BOT_ONLY_PACKAGES


def test_the_api_does_not_depend_on_the_bots_packages() -> None:
    project = tomllib.loads((API_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    declared = {
        re.split(r"[<>=!~\[ ;]", dependency, maxsplit=1)[0].lower().replace("_", "-")
        for dependency in project["project"]["dependencies"]
    }

    assert not declared & {"telethon", "playwright", "aiogram", "aiosqlite", "pyyaml"}
    lock = (API_ROOT / "uv.lock").read_text(encoding="utf-8")
    for package in ("telethon", "playwright", "aiogram", "aiosqlite"):
        assert f'name = "{package}"' not in lock


def test_the_api_has_its_own_lockfile_and_python_range() -> None:
    project = tomllib.loads((API_ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert (API_ROOT / "uv.lock").exists()
    assert project["project"]["requires-python"] == ">=3.12,<3.13"
