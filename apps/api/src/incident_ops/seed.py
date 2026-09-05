"""Deterministic fixture command contract; records are added in later phases."""

import argparse

from incident_ops.config import get_settings


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Load deterministic Incident Ops fixtures")
    parser.add_argument(
        "--reset",
        action="store_true",
        help="delete fixture-owned development data before loading",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    settings = get_settings()

    if args.reset and (settings.app_env != "development" or not settings.allow_development_reset):
        raise SystemExit(
            "Fixture reset refused: require APP_ENV=development and ALLOW_DEVELOPMENT_RESET=true"
        )

    mode = "reset then additive" if args.reset else "additive"
    print(f"Fixture mode: {mode}. No Phase 1 records to load.")


if __name__ == "__main__":
    main()
