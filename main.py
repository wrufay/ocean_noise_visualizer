"""
Scotian Shelf AIS Vessel Tracker — FastAPI backend.

SQLite (data/ais.db) by default.
Set DATABASE_URL to use Postgres (e.g. when running via docker-compose).

Run locally:  uvicorn main:app --reload
"""

import os
import sqlite3
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

DB_PATH = Path(__file__).parent / "data" / "ais.db"
DATABASE_URL: str = os.environ.get("DATABASE_URL", "")

# Populated at startup — only MMSIs in the DB are served.
ALLOWED_MMSIS: set[int] = set()


def to_epoch(dt_str: str) -> int:
    """Convert ISO datetime string to unix timestamp, treating naive strings as UTC."""
    dt = datetime.fromisoformat(dt_str)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def sq(sql: str, params: list | None = None) -> list[dict]:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(sql, params or []).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def pq(sql: str, params: list | None = None) -> list[dict]:
    import psycopg2
    import psycopg2.extras
    with psycopg2.connect(DATABASE_URL) as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params or [])
            return [dict(r) for r in cur.fetchall()]


def query(sql: str, params: list | None = None) -> list[dict]:
    """Route to Postgres or SQLite. Write SQL with ? placeholders."""
    if DATABASE_URL:
        return pq(sql.replace("?", "%s"), params)
    return sq(sql, params)


@asynccontextmanager
async def lifespan(_: FastAPI):
    global ALLOWED_MMSIS
    # MMSI range 200000000–799999999 = real vessels (excludes buoys/beacons)
    sql = "SELECT DISTINCT mmsi FROM ais_202503_static WHERE mmsi BETWEEN 200000000 AND 799999999 ORDER BY mmsi"
    try:
        ALLOWED_MMSIS = {r["mmsi"] for r in query(sql)}
        print(f"Loaded {len(ALLOWED_MMSIS)} vessels ({'postgres' if DATABASE_URL else 'sqlite'}).")
    except Exception as e:
        print(f"Warning: could not load vessels from DB ({e}).")
    yield


app = FastAPI(lifespan=lifespan)

CORS_ORIGINS = os.environ.get(
    "CORS_ORIGINS",
    "https://vesselviz.vercel.app,http://localhost:3000,http://localhost:5173",
).split(",")
app.add_middleware(
    CORSMiddleware, allow_origins=CORS_ORIGINS, allow_methods=["*"], allow_headers=["*"]
)


@app.get("/")
def root():
    return {"message": "app is running", "db": "postgres" if DATABASE_URL else "sqlite"}


@app.get("/api/vessels")
def get_vessels():
    if DATABASE_URL:
        rows = pq("""
            SELECT DISTINCT ON (mmsi) mmsi, vessel_name, ship_type, 'CCG' AS source
            FROM ais_202503_static WHERE mmsi IS NOT NULL
            ORDER BY mmsi, CASE WHEN vessel_name IS NOT NULL AND vessel_name != '' THEN 0 ELSE 1 END
        """)
    else:
        rows = sq("""
            SELECT mmsi, vessel_name, ship_type, 'CCG' AS source
            FROM (
                SELECT mmsi, vessel_name, ship_type,
                       ROW_NUMBER() OVER (
                           PARTITION BY mmsi
                           ORDER BY CASE WHEN vessel_name IS NOT NULL AND vessel_name != '' THEN 0 ELSE 1 END
                       ) AS rn
                FROM ais_202503_static WHERE mmsi IS NOT NULL
            ) WHERE rn = 1
        """)
    seen: set[int] = set()
    vessels = []
    for r in rows:
        if r["mmsi"] in ALLOWED_MMSIS and r["mmsi"] not in seen:
            seen.add(r["mmsi"])
            vessels.append({
                "mmsi": r["mmsi"],
                "vessel_name": r["vessel_name"],
                "ship_type": r["ship_type"],
                "source": r["source"],
            })
    return {"vessels": vessels, "count": len(vessels)}


@app.get("/api/vessel/{mmsi}/route")
def get_vessel_route(
    mmsi: int,
    start: str | None = Query(None),
    end: str | None = Query(None),
):
    if ALLOWED_MMSIS and mmsi not in ALLOWED_MMSIS:
        raise HTTPException(status_code=404, detail="Vessel not found")

    sql = "SELECT time, longitude, latitude, sog, cog FROM ais_202503_dynamic WHERE mmsi = ?"
    params: list = [mmsi]

    # Convert ISO strings to epoch ints — same comparison works in both SQLite and Postgres.
    if start:
        sql += " AND time >= ?"
        params.append(to_epoch(start))
    if end:
        sql += " AND time <= ?"
        params.append(to_epoch(end))

    sql += " ORDER BY time"

    points = [
        {
            "time": r["time"],
            "latitude": r["latitude"],
            "longitude": r["longitude"],
            "sog": r["sog"],
            "cog": r["cog"],
        }
        for r in query(sql, params)
    ]
    return {"mmsi": mmsi, "points": points, "count": len(points)}
