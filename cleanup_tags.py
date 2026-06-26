#!/usr/bin/env python3
"""
One-time cleanup for databases created before EXCLUDED_TAGS existed.

Removes the niche/rare tags listed in backend/crud.py:EXCLUDED_TAGS from an
already-populated codecoach.db, without touching problems, submissions, users,
or rating history -- so you don't have to wipe the whole database and re-fetch
the full Codeforces problem set + everyone's submission history just to drop
15 rows.

Run once, from the project root:
    python cleanup_tags.py
"""
import sys
import pathlib

ROOT = pathlib.Path(__file__).parent
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(ROOT))

from models import SessionLocal, Tag, ProblemTag  # noqa: E402
from crud import EXCLUDED_TAGS  # noqa: E402


def main():
    db = SessionLocal()
    try:
        tags_to_remove = db.query(Tag).filter(Tag.tag_name.in_(EXCLUDED_TAGS)).all()
        if not tags_to_remove:
            print("Nothing to clean up -- no excluded tags found in the database.")
            return

        removed_names = []
        for tag in tags_to_remove:
            db.query(ProblemTag).filter(ProblemTag.tag_id == tag.id).delete()
            removed_names.append(tag.tag_name)
            db.delete(tag)

        db.commit()
        print(f"Removed {len(removed_names)} excluded tags from the database:")
        for name in removed_names:
            print(f"  - {name}")
        print("\nDone. Restart the app (python run.py) and reload your profile "
              "to see the cleaned-up skill table.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
