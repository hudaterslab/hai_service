import hashlib
import os
from pathlib import Path

import psycopg


BASE_DIR = Path(__file__).resolve().parent
MIGRATIONS_DIR = BASE_DIR / "migrations"
DATABASE_URL = os.getenv("DATABASE_URL", "postgres://vms:vms@localhost:5432/vms?sslmode=disable")


def migration_hash(sql: str) -> str:
    return hashlib.sha256(sql.encode("utf-8")).hexdigest()


def ensure_table(cur):
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
          version TEXT PRIMARY KEY,
          checksum_sha256 TEXT NOT NULL,
          applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )


def applied_versions(cur) -> set[str]:
    cur.execute("SELECT version FROM schema_migrations")
    return {r[0] for r in cur.fetchall()}


def main():
    files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    if not files:
        print("No migrations found.")
        return

    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            ensure_table(cur)
            done = applied_versions(cur)
            for f in files:
                version = f.name
                if version in done:
                    continue
                sql = f.read_text(encoding="utf-8")
                checksum = migration_hash(sql)
                print(f"Applying {version} ...")
                cur.execute(sql)
                cur.execute(
                    "INSERT INTO schema_migrations (version, checksum_sha256) VALUES (%s, %s)",
                    (version, checksum),
                )
            conn.commit()
    print("Migration complete.")


if __name__ == "__main__":
    main()
