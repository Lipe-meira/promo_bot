"""Shared shadow database path rules. Legacy filename retained for existing previews."""

from pathlib import Path

from sqlalchemy.engine import make_url

from promo_bot.config.settings import EnvironmentSettings


def resolve_shadow_database_path(
    settings: EnvironmentSettings, explicit_path: Path | None = None
) -> Path:
    path = explicit_path or settings.resolved_runtime_dir / "shadow" / "aliexpress-shadow.sqlite3"
    resolved = path.expanduser().resolve()
    root = Path(__file__).resolve().parents[3]
    if resolved == root or root in resolved.parents:
        raise ValueError("ALIEXPRESS_SHADOW_DATABASE_MUST_BE_EXTERNAL")
    if resolved.suffix.casefold() not in {".sqlite", ".sqlite3", ".db"}:
        raise ValueError("ALIEXPRESS_SHADOW_DATABASE_EXTENSION_INVALID")
    main_url = make_url(settings.resolved_database_url)
    if main_url.database and main_url.database != ":memory:":
        if resolved == Path(main_url.database).expanduser().resolve():
            raise ValueError("ALIEXPRESS_SHADOW_DATABASE_MUST_NOT_BE_MAIN")
    return resolved


def shadow_database_url(path: Path) -> str:
    return f"sqlite+aiosqlite:///{path.as_posix()}"
