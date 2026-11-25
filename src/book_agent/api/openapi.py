"""Write the API's OpenAPI schema, for generating the UI's TypeScript types (D-043).

    uv run python -m book_agent.api.openapi [--out openapi.json]

Needs no settings or API keys: the schema comes from the routes and models alone.
"""

import argparse
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from book_agent.api.app import create_app


def schema() -> dict[str, Any]:
    return create_app().openapi()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m book_agent.api.openapi")
    parser.add_argument("--out", type=Path, help="write here instead of standard output")
    args = parser.parse_args(argv)
    text = json.dumps(schema(), indent=2, ensure_ascii=False) + "\n"
    if args.out:
        args.out.write_text(text, encoding="utf-8")
        print(f"Wrote {args.out}")
    else:
        print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
