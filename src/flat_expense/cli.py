import argparse
import json
import os
from pathlib import Path

from flat_expense import service as s
from flat_expense.db import make_sessions
from flat_expense.importer import import_xlsx


def main():
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    importer = commands.add_parser("import-xlsx")
    importer.add_argument("path", type=Path)
    importer.add_argument("--owner", required=True, type=int)
    importer.add_argument("--dry-run", action="store_true")
    seed = commands.add_parser("seed")
    seed.add_argument("--owner", required=True, type=int)
    args = parser.parse_args()
    sessions = make_sessions(os.environ["DATABASE_URL"])
    allowed = {int(x) for x in os.environ["ALLOWED_TELEGRAM_USER_IDS"].split(",") if x.strip()}
    if args.owner not in allowed:
        raise SystemExit("Владелец не включён в ALLOWED_TELEGRAM_USER_IDS.")
    try:
        with sessions() as db:
            if args.command == "import-xlsx":
                result = import_xlsx(db, args.owner, args.path)
                if args.dry_run:
                    db.rollback()
                else:
                    db.commit()
                print(json.dumps({**result, "dry_run": args.dry_run}, ensure_ascii=False))
            else:
                s.seed_categories(db, args.owner)
                db.commit()
                print("Категории созданы.")
    except s.DomainError as exc:
        raise SystemExit(str(exc)) from None


if __name__ == "__main__":
    main()
