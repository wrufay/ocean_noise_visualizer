#!/usr/bin/env python3
"""
AIS data ingestion pipeline for Scotian Shelf vessel tracking.

Two data sources:
  1. CCG terrestrial NMEA files  → decoded via aisdb
  2. exactEarth satellite CSVs   → filtered via DuckDB

Set DATABASE_URL to use Postgres, otherwise falls back to SQLite at data/ais.db.
"""

import glob
import os
import sqlite3
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

import duckdb
import aisdb
from aisdb.database.sqlfcn_callbacks import in_time_bbox_validmmsi

# Scotian Shelf bounding box - used for filtering data to only vessels on the Scotian Shelf
XMIN, XMAX = -66.0, -57.0
YMIN, YMAX = 42.0, 47.0

# Hard coded directories - change these to point to your local data
CCG_DIR = "/home/shared/aisdecode/testData"
SAT_DIR = "/home/shared/aisdecode/testData/newSatAis/01"

SQLITE_PATH = Path(__file__).parent.parent / "data" / "ais.db"

# Set DATABASE_URL to use Postgres; if unset, falls back to SQLite
DATABASE_URL: str = os.environ.get("DATABASE_URL", "")

USE_POSTGRES = DATABASE_URL is not None


def get_aisdb_conn():
    if USE_POSTGRES:
        from aisdb.database.dbconn import PostgresDBConn
        return PostgresDBConn(DATABASE_URL)
    else:
        return aisdb.SQLiteDBConn(str(SQLITE_PATH))


def get_sat_conn():
    """Return a DB-API connection for satellite table operations."""
    if USE_POSTGRES:
        import psycopg
        return psycopg.connect(DATABASE_URL)
    else:
        return sqlite3.connect(str(SQLITE_PATH))


def placeholder():
    return "%s" if USE_POSTGRES else "?"

# Use AISdb to decode CCG NMEA files, then filter to Scotian Shelf bounding box.
def ingest_ccg():
    """
    Decode raw CCG NMEA files into the database via aisdb.

    aisdb detects file type by extension: .csv = decoded tabular, .nm4 = raw NMEA.
    CCG files are raw NMEA saved as .csv, so we symlink them as .nm4 before decoding.
    """
    csv_files = glob.glob(f"{CCG_DIR}/CCG_AIS_UTC_Log_*.csv")
    if not csv_files:
        print("No CCG files found.")
        return

    print(f"Ingesting {len(csv_files)} CCG file(s) via {'Postgres' if USE_POSTGRES else 'SQLite'}...")

    with tempfile.TemporaryDirectory() as tmpdir:
        nm4_files = []

        # Use symlink to change file extensions from .csv to .nm4 so AISdb decodes them as raw NMEA instead of pre-decoded tabular.
        for csv_path in csv_files:
            stem = Path(csv_path).stem
            nm4_path = os.path.join(tmpdir, f"{stem}.nm4")
            os.symlink(os.path.abspath(csv_path), nm4_path)
            nm4_files.append(nm4_path)

        # AISdb documentation and source code for the decode_msgs function: https://aisviz.cs.dal.ca/AISdb/api/aisdb.database.decoder.html
        # Tutorial describing the steps to process .csv or .nm4 files and write to a SQLite database: https://aisviz.gitbook.io/documentation/tutorials/using-your-ais-data
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
    After aisdb decodes ALL global CCG messages, delete rows outside
    the Scotian Shelf bounding box from both dynamic and static tables.
    Also removes vessels from static that have no dynamic pings in the shelf.
    """
    print("Trimming CCG tables to Scotian Shelf bounding box...")
    with sqlite3.connect(str(SQLITE_PATH)) as conn:
        # trim dynamic pings outside bounding box
        cur = conn.execute(f"""
            DELETE FROM ais_202503_dynamic
            WHERE latitude  NOT BETWEEN {YMIN} AND {YMAX}
               OR longitude NOT BETWEEN {XMIN} AND {XMAX}
        """)
        print(f"  Removed {cur.rowcount:,} out-of-bounds dynamic rows.")

        # remove static entries for vessels with no shelf pings
        cur = conn.execute("""
            DELETE FROM ais_202503_static
            WHERE mmsi NOT IN (SELECT DISTINCT mmsi FROM ais_202503_dynamic)
        """)
        print(f"  Removed {cur.rowcount:,} out-of-bounds static rows.")
        conn.commit()

    print("Trim complete.")


def ingest_satellite():
    """
    Filter exactEarth satellite CSVs to Scotian Shelf bounding box
    and insert into the database.

    DuckDB 1.5 doesn't support zip natively, so we extract each zip to a
    temp file, filter with DuckDB, append to the DB, then clean up.
    """
    # Each day of satellite data is one zip file containing a single CSV
    zip_files = sorted(glob.glob(f"{SAT_DIR}/*.csv.zip"))
    if not zip_files:
        print("No satellite zip files found.")
        return

    print(f"Filtering {len(zip_files)} satellite file(s) via {'Postgres' if USE_POSTGRES else 'SQLite'}...")

    p = placeholder()
    # Create the table once before the loop; IF NOT EXISTS is safe to re-run
    with get_sat_conn() as conn:
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS ais_satellite (
                mmsi        {'BIGINT' if USE_POSTGRES else 'INTEGER'},
                time        TEXT,
                longitude   {'DOUBLE PRECISION' if USE_POSTGRES else 'REAL'},
                latitude    {'DOUBLE PRECISION' if USE_POSTGRES else 'REAL'},
                sog         {'DOUBLE PRECISION' if USE_POSTGRES else 'REAL'},
                cog         {'DOUBLE PRECISION' if USE_POSTGRES else 'REAL'},
                vessel_name TEXT,
                ship_type   TEXT
            )
        """)
        # Index on (mmsi, time) speeds up route lookups per vessel
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sat_mmsi_time ON ais_satellite (mmsi, time)"
        )
        conn.commit()

    total = 0
    # Use a temp dir so extracted CSVs are cleaned up automatically on exit
    with tempfile.TemporaryDirectory() as tmpdir:
        for zip_path in zip_files:
            # Extract the single CSV inside the zip to the temp dir
            with zipfile.ZipFile(zip_path) as z:
                csv_name = z.namelist()[0]
                extracted = os.path.join(tmpdir, csv_name)
                z.extract(csv_name, tmpdir)

            # DuckDB filters in-memory before returning rows — much faster than
            # inserting everything and deleting out-of-bounds rows after the fact.
            # TRY_CAST handles malformed values without crashing (sets them to NULL).

            # DuckDB read_csv documentation: https://duckdb.org/docs/current/data/csv/overview
            con = duckdb.connect()
            df = con.execute(f"""
                SELECT
                    TRY_CAST(MMSI AS BIGINT)      AS mmsi,
                    Time                           AS time,
                    TRY_CAST(Longitude AS DOUBLE)  AS longitude,
                    TRY_CAST(Latitude  AS DOUBLE)  AS latitude,
                    TRY_CAST(SOG AS DOUBLE)        AS sog,
                    TRY_CAST(COG AS DOUBLE)        AS cog,
                    Vessel_Name                    AS vessel_name,
                    Ship_Type                      AS ship_type
                FROM read_csv('{extracted}', header=true, ignore_errors=true)
                WHERE
                    TRY_CAST(Latitude  AS DOUBLE) BETWEEN {YMIN} AND {YMAX}
                    AND TRY_CAST(Longitude AS DOUBLE) BETWEEN {XMIN} AND {XMAX}
                    AND TRY_CAST(MMSI AS BIGINT) IS NOT NULL
            """).df()
            con.close()
            os.remove(extracted)  # free disk space immediately after filtering

            if len(df) > 0:
                rows = list(df.itertuples(index=False, name=None))
                with get_sat_conn() as conn:
                    cur = conn.cursor()
                    # executemany batches all rows in one call per file
                    cur.executemany(f"""
                        INSERT INTO ais_satellite
                            (mmsi, time, longitude, latitude, sog, cog, vessel_name, ship_type)
                        VALUES ({p}, {p}, {p}, {p}, {p}, {p}, {p}, {p})
                    """, rows)
                    conn.commit()
                total += len(df)

            print(f"  {os.path.basename(zip_path)}: {len(df):,} records")

    print(f"Satellite ingestion complete. {total:,} total records.")


# Used for testing and printing sample rows after ingestion, not currently used by API endpoints.
def query_scotian_shelf(start: datetime, end: datetime):
    """
    Query CCG data filtered to Scotian Shelf bounding box and time range.
    Returns a generator of row batches.
    """
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
    ingest_satellite()

    print("\nSample CCG query (first 3 rows):")
    start = datetime(2025, 3, 11)
    end = datetime(2025, 3, 14)
    for rows in query_scotian_shelf(start, end):
        for row in list(rows)[:3]:
            print(dict(row))
        break
