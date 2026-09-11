"""Keep dependency payload/SQL logging out of explicit shadow delivery commands."""

import logging
from collections.abc import Iterator
from contextlib import contextmanager

_active_scopes = 0
_previous_levels: dict[str, int] = {}


@contextmanager
def mute_shadow_payload_logs() -> Iterator[None]:
    # Entry/exit contain no await; overlapping tasks share one suppression lifetime.
    global _active_scopes
    prefixes = ("telegram", "httpx", "httpcore", "aiosqlite", "sqlalchemy")
    names = set(prefixes) | {
        name
        for name in logging.Logger.manager.loggerDict
        if any(name.startswith(prefix + ".") for prefix in prefixes)
    }
    _active_scopes += 1
    try:
        for name in names:
            _previous_levels.setdefault(name, logging.getLogger(name).level)
            logging.getLogger(name).setLevel(logging.CRITICAL + 1)
        yield
    finally:
        _active_scopes -= 1
        if _active_scopes == 0:
            for name, level in _previous_levels.items():
                logging.getLogger(name).setLevel(level)
            _previous_levels.clear()
