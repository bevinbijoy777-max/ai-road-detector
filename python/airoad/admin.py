"""Driver administration: ``python -m airoad.admin <command>``.

    python -m airoad.admin create-driver "Asha"   # prints the first token
    python -m airoad.admin add-token <driver_id>  # extra token, for rotation
    python -m airoad.admin list-drivers
    python -m airoad.admin revoke-token <token>

Tokens are printed once: only their SHA-256 hash is stored, so a lost token has to
be replaced rather than looked up.
"""

from __future__ import annotations

import argparse
import sys
from typing import Optional, Sequence

from airoad.db import StorageNotConfigured
from airoad.drivers import DriverStore, create_driver_store


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="airoad.admin",
        description="Manage drivers and their report sync tokens.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    create = commands.add_parser(
        "create-driver", help="create a driver and print its first token"
    )
    create.add_argument("name", help="driver name, shown in list-drivers")

    add_token = commands.add_parser(
        "add-token", help="issue another token for a driver (rotation)"
    )
    add_token.add_argument("driver_id")

    commands.add_parser("list-drivers", help="list drivers and their token counts")

    revoke = commands.add_parser("revoke-token", help="delete one token")
    revoke.add_argument("token")

    return parser


def main(argv: Optional[Sequence[str]] = None, store: Optional[DriverStore] = None) -> int:
    args = build_parser().parse_args(argv)

    if store is None:
        try:
            store = create_driver_store()
        except StorageNotConfigured as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2

    if args.command == "create-driver":
        try:
            driver, token = store.create_driver(args.name)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        print(f"driver_id: {driver.driver_id}")
        print(f"name:      {driver.name}")
        print(f"token:     {token}")
        print("store this token now - only its hash is kept", file=sys.stderr)
        return 0

    if args.command == "add-token":
        try:
            token = store.add_token(args.driver_id)
        except KeyError:
            print(f"error: no driver {args.driver_id}", file=sys.stderr)
            return 2
        print(f"token: {token}")
        print("store this token now - only its hash is kept", file=sys.stderr)
        return 0

    if args.command == "list-drivers":
        rows = store.list_drivers()
        if not rows:
            print("no drivers yet - create one with: create-driver NAME")
            return 0
        for row in rows:
            print(f"{row['driver_id']}  {row['name']}  tokens={row['tokens']}")
        return 0

    if args.command == "revoke-token":
        if store.revoke_token(args.token):
            print("revoked")
            return 0
        print("error: no such token", file=sys.stderr)
        return 2

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
