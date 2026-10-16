"""Create a user from the command line (e.g. the first administrator in production).

    python -m scripts.create_user admin "Plant Administrator" ADMIN

The password is read from the IMPLANTTRACE_PASSWORD environment variable, or prompted for.
"""

import argparse
import getpass
import os
import sys

from app.db import SessionLocal
from app.errors import Conflict, ValidationFailed
from app.models import Role
from app.schemas import UserCreate
from app.services.users import create_user


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("username")
    parser.add_argument("full_name")
    parser.add_argument("role", choices=[r.value for r in Role])
    args = parser.parse_args()

    password = os.environ.get("IMPLANTTRACE_PASSWORD") or getpass.getpass("Password: ")
    with SessionLocal() as db:
        db.info["user"] = "cli:create_user"
        try:
            create_user(
                db,
                UserCreate(
                    username=args.username,
                    full_name=args.full_name,
                    role=Role(args.role),
                    password=password,
                ),
            )
        except (Conflict, ValidationFailed) as exc:
            sys.exit(f"Error: {exc}")
        db.commit()
    print(f"Created {args.role} user {args.username}")


if __name__ == "__main__":
    main()
