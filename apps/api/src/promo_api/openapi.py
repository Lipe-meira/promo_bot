"""Write the OpenAPI contract: `uv run python -m promo_api.openapi [--check]`.

The document is generated from the pydantic models and committed as `apps/api/openapi.json`;
the web app derives its TypeScript types from it. No database or network is used.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from promo_api.app import create_app
from promo_api.settings import ApiSettings

TARGET = Path(__file__).resolve().parents[2] / "openapi.json"


def render() -> str:
    settings = ApiSettings(
        environment="development",
        database_url="postgresql+asyncpg://contract:contract@127.0.0.1:1/contract",
        clerk_issuer="https://contract.invalid",
        clerk_authorized_parties=("http://localhost:3000",),
        # Synthetic placeholder: generating the contract needs a well-formed key, never a real one.
        clerk_secret_key="sk_test_contract_generation_only",
    )
    return json.dumps(create_app(settings).openapi(), indent=2, ensure_ascii=False) + "\n"


def main(argv: list[str]) -> int:
    document = render()
    if "--check" in argv:
        current = TARGET.read_text(encoding="utf-8") if TARGET.exists() else ""
        if current != document:
            print("openapi.json is out of date", file=sys.stderr)
            return 1
        return 0
    TARGET.write_text(document, encoding="utf-8", newline="\n")
    print(f"wrote {TARGET}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
