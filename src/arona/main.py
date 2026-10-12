"""Thin entrypoint. Full Core lifecycle is intentionally deferred to Phase 4."""

from __future__ import annotations

import logging

from arona.operations.cli import run


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    return run(argv)


if __name__ == "__main__":
    raise SystemExit(main())
