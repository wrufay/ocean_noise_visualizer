#!/usr/bin/env python3
"""
AIS data ingestion pipeline for Scotian Shelf vessel tracking.

Data source: CCG terrestrial NMEA files, decoded via aisdb.

Set DATABASE_URL to use Postgres, otherwise falls back to SQLite at data/ais.db.
"""

import glob
import os
import sqlite3
import tempfile
from datetime import datetime
from pathlib import Path

import aisdb
from aisdb.database.sqlfcn_callbacks import in_time_bbox_validmmsi

# Scotian Shelf bounding box
XMIN, XMAX = -66.0, -57.0
YMIN, YMAX = 42.0, 47.0

# Hard coded directory — change this to point to your local CCG data
CCG_DIR = "/home/shared/aisdecode/testData"

SQLITE_PATH = Path(__file__).parent.parent / "data" / "ais.db"

# Set to an int to keep only N vessels in the output DB (safe to commit).
# Set to None to keep all data (local practice only — don't commit).
DEMO_SAMPLE = 10

# Set DATABASE_URL to use Postgres; if unset, falls back to SQLite
DATABASE_URL: str = os.environ.get("DATABASE_URL", "")
USE_POSTGRES = bool(DATABASE_URL)


def get_aisdb_conn():
    if USE_POSTGRES:
        from aisdb.database.dbconn import PostgresDBConn
        return PostgresDBConn(DATABASE_URL)
    else:
        return aisdb.SQLiteDBConn(str(SQLITE_PATH))


def ingest_ccg():
    """
    Decode raw CCG NMEA files into the database via aisdb.

    aisdb detects file type by extension: .csv = decoded tabular, .nm4 = raw NMEA.
    CCG files are raw NMEA saved as .csv, so we symlink them as .nm4 before decoding.
    """
    csv_files = sorted(glob.glob(f"{CCG_DIR}/CCG_AIS_UTC_Log_*.csv"))[:1]
    if not csv_files:
        print("No CCG files found.")
        return

    print(f"Ingesting {len(csv_files)} CCG file(s) via {'Postgres' if USE_POSTGRES else 'SQLite'}...")

    with tempfile.TemporaryDirectory() as tmpdir:
        nm4_files = []

        # Symlink .csv → .nm4 so aisdb treats them as raw NMEA instead of decoded tabular
        for csv_path in csv_files:
            stem = Path(csv_path).stem
            nm4_path = os.path.join(tmpdir, f"{stem}.nm4")
            os.symlink(os.path.abspath(csv_path), nm4_path)
            nm4_files.append(nm4_path)

        # https://aisviz.cs.dal.ca/AISdb/api/aisdb.database.decoder.html
        with get_aisdb_conn() as dbconn:
            aisdb.decode_msgs(
                filepaths=nm4_files,
                dbconn=dbconn,
                source="CCG_terrestrial",
                skip_checksum=True,
                type_preference="nmea",
            )

    print("CCG ingestion complete.")
    trim_ccg_to_scotian_shelf()


def trim_ccg_to_scotian_shelf():
    """
    aisdb decodes everything globally — delete rows outside the Scotian Shelf
    bounding box, then cascade delete vessels with no remaining pings.
    """
    print("Trimming CCG tables to Scotian Shelf bounding box...")
    with sqlite3.connect(str(SQLITE_PATH)) as conn:
        cur = conn.execute(f"""
            DELETE FROM ais_202503_dynamic
            WHERE latitude  NOT BETWEEN {YMIN} AND {YMAX}
               OR longitude NOT BETWEEN {XMIN} AND {XMAX}
        """)
        print(f"  Removed {cur.rowcount:,} out-of-bounds dynamic rows.")

        cur = conn.execute("""
            DELETE FROM ais_202503_static
            WHERE mmsi NOT IN (SELECT DISTINCT mmsi FROM ais_202503_dynamic)
        """)
        print(f"  Removed {cur.rowcount:,} out-of-bounds static rows.")
        conn.commit()

    print("Trim complete.")


def sample_to_demo(n: int):
    """
    Keep only N distinct CCG vessels so the DB is small and safe to commit.
    Runs automatically after ingestion when DEMO_SAMPLE is set.
    """
    print(f"\nSampling to {n} CCG vessels for demo DB...")
    with sqlite3.connect(str(SQLITE_PATH)) as conn:
        mmsis = [r[0] for r in conn.execute(
            "SELECT DISTINCT mmsi FROM ais_202503_dynamic WHERE mmsi > 100000000 ORDER BY mmsi LIMIT ?", (n,)
        ).fetchall()]

        if mmsis:
            placeholders = ",".join("?" * len(mmsis))
            conn.execute(f"DELETE FROM ais_202503_dynamic WHERE mmsi NOT IN ({placeholders})", mmsis)
            conn.execute(f"DELETE FROM ais_202503_static  WHERE mmsi NOT IN ({placeholders})", mmsis)
            print(f"  Kept {len(mmsis)} vessels: {mmsis}")

        conn.commit()

    with sqlite3.connect(str(SQLITE_PATH)) as conn:
        conn.execute("VACUUM")
    print("  VACUUM complete. DB is ready to commit.")


def query_scotian_shelf(start: datetime, end: datetime):
    """Query CCG data filtered to Scotian Shelf bounding box and time range."""
    with get_aisdb_conn() as dbconn:
        q = aisdb.DBQuery(
            dbconn=dbconn,
            callback=in_time_bbox_validmmsi,
            start=start,
            end=end,
            xmin=XMIN, xmax=XMAX,
            ymin=YMIN, ymax=YMAX,
        )
        for rows in q.gen_qry():
            yield rows


if __name__ == "__main__":
    if not USE_POSTGRES:
        SQLITE_PATH.parent.mkdir(parents=True, exist_ok=True)
        print("No DATABASE_URL set — using SQLite at", SQLITE_PATH)

    ingest_ccg()

    if DEMO_SAMPLE:
        sample_to_demo(DEMO_SAMPLE)

    print("\nSample CCG query (first 3 rows):")
    start = datetime(2025, 3, 11)
    end = datetime(2025, 3, 14)
    for rows in query_scotian_shelf(start, end):
        for row in list(rows)[:3]:
            print(dict(row))
        break
