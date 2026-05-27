#!/usr/bin/env python3
"""
Migrate data/ais.db (SQLite) to Neon Postgres via HTTP API.
Run once after ingestion. Set NEON_CONNECTION_STRING before running.

    export NEON_CONNECTION_STRING="postgresql://..."
    python3 pipeline/migrate_to_neon.py
"""

import os
import re
import sqlite3
from pathlib import Path

import httpx

SQLITE_PATH = Path(__file__).parent.parent / "data" / "ais.db"
NEON_CONN = os.environ.get("NEON_CONNECTION_STRING")
if not NEON_CONN:
    raise SystemExit("Error: NEON_CONNECTION_STRING not set.")

NEON_URL = f"https://{re.search(r'@([^/?]+)', NEON_CONN).group(1)}/sql"
HEADERS = {"Neon-Connection-String": NEON_CONN, "Content-Type": "application/json"}
BATCH_SIZE = 1000


def nq(sql: str, params: list | None = None):
    body: dict = {"query": sql}
    if params:
        body["params"] = [p for p in params]
    r = httpx.post(NEON_URL, json=body, headers=HEADERS, timeout=60)
    r.raise_for_status()
    return r.json()


def create_tables():
    print("Creating tables...")
    nq("""
        CREATE TABLE IF NOT EXISTS ais_202503_dynamic (
            mmsi       BIGINT NOT NULL,
            time       BIGINT NOT NULL,
            longitude  DOUBLE PRECISION NOT NULL,
            latitude   DOUBLE PRECISION NOT NULL,
            rot        DOUBLE PRECISION,
            sog        DOUBLE PRECISION,
            cog        DOUBLE PRECISION,
            heading    DOUBLE PRECISION,
            maneuver   INTEGER,
            utc_second INTEGER,
            source     TEXT NOT NULL
        )
    """)
    nq("CREATE INDEX IF NOT EXISTS idx_dyn_mmsi_time ON ais_202503_dynamic (mmsi, time)")

    nq("""
        CREATE TABLE IF NOT EXISTS ais_202503_static (
            mmsi         BIGINT NOT NULL,
            time         BIGINT NOT NULL,
            vessel_name  TEXT,
            ship_type    INTEGER,
            call_sign    TEXT,
            imo          BIGINT NOT NULL DEFAULT 0,
            dim_bow      INTEGER,
            dim_stern    INTEGER,
            dim_port     INTEGER,
            dim_star     INTEGER,
            draught      INTEGER,
            destination  TEXT,
            ais_version  INTEGER,
            fixing_device TEXT,
            eta_month    INTEGER,
            eta_day      INTEGER,
            eta_hour     INTEGER,
            eta_minute   INTEGER,
            source       TEXT NOT NULL
        )
    """)
    nq("CREATE INDEX IF NOT EXISTS idx_static_mmsi ON ais_202503_static (mmsi)")

    nq("""
        CREATE TABLE IF NOT EXISTS ais_satellite (
            mmsi        BIGINT,
            time        TEXT,
            longitude   DOUBLE PRECISION,
            latitude    DOUBLE PRECISION,
            sog         DOUBLE PRECISION,
            cog         DOUBLE PRECISION,
            vessel_name TEXT,
            ship_type   TEXT
        )
    """)
    nq("CREATE INDEX IF NOT EXISTS idx_sat_mmsi_time ON ais_satellite (mmsi, time)")
    print("Tables ready.")


def migrate_table(conn: sqlite3.Connection, table: str, columns: list[str]):
    total = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    print(f"\n{table}: {total:,} rows")

    cursor = conn.cursor()
    cursor.execute(f"SELECT {', '.join(columns)} FROM {table}")

    inserted = 0
    while True:
        batch = cursor.fetchmany(BATCH_SIZE)
        if not batch:
            break

        ncols = len(columns)
        value_clauses, params = [], []
        for i, row in enumerate(batch):
            placeholders = ", ".join(f"${i * ncols + j + 1}" for j in range(ncols))
            value_clauses.append(f"({placeholders})")
            params.extend(row)

        sql = (
            f"INSERT INTO {table} ({', '.join(columns)}) "
            f"VALUES {', '.join(value_clauses)}"
        )
        nq(sql, params)
        inserted += len(batch)
        print(f"  {inserted:,} / {total:,}", end="\r", flush=True)

    print(f"  {inserted:,} / {total:,}  done")


if __name__ == "__main__":
    create_tables()

    conn = sqlite3.connect(str(SQLITE_PATH))

    migrate_table(conn, "ais_202503_dynamic", [
        "mmsi", "time", "longitude", "latitude",
        "rot", "sog", "cog", "heading", "maneuver", "utc_second", "source",
    ])
    migrate_table(conn, "ais_202503_static", [
        "mmsi", "time", "vessel_name", "ship_type", "call_sign", "imo",
        "dim_bow", "dim_stern", "dim_port", "dim_star", "draught", "destination",
        "ais_version", "fixing_device", "eta_month", "eta_day", "eta_hour", "eta_minute", "source",
    ])
    migrate_table(conn, "ais_satellite", [
        "mmsi", "time", "longitude", "latitude", "sog", "cog", "vessel_name", "ship_type",
    ])

    conn.close()
    print("\nMigration complete.")
