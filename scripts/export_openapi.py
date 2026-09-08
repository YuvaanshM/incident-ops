"""Export the development API contract with deterministic JSON formatting."""

import argparse
import asyncio
import json
from pathlib import Path

from incident_ops.config import Settings
from incident_ops.main import create_app

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "packages" / "api-client" / "openapi.json"


def render_openapi() -> str:
    settings = Settings(
        _env_file=None,
        app_env="development",
        database_url="postgresql+asyncpg://openapi:openapi@localhost/openapi",
        development_auth_enabled=True,
        cors_origins=["http://localhost:3000"],
        allowed_hosts=["localhost", "testserver"],
    )
    app = create_app(settings)
    document = (
        json.dumps(app.openapi(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    )
    asyncio.run(app.state.database_engine.dispose())
    return document


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--check",
        action="store_true",
        help="fail instead of writing when the committed contract is stale",
    )
    arguments = parser.parse_args()
    rendered = render_openapi()
    if arguments.check:
        if not OUTPUT.exists() or OUTPUT.read_text() != rendered:
            print("OpenAPI contract is stale; run `make export-openapi`.")
            return 1
        return 0
    OUTPUT.write_text(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
