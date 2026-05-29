import os
import re
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

# uvicorn main:app --reload
# Set NEON_CONNECTION_STRING env var to use Neon Postgres, otherwise falls back to SQLite.

DB_PATH = Path(__file__).parent / "data" / "ais.db"
NEON_CONN: str = os.environ.get("NEON_CONNECTION_STRING", "")
_neon_host_match = re.search(r'@([^/?]+)', NEON_CONN)
NEON_URL = f"https://{_neon_host_match.group(1)}/sql" if _neon_host_match else ""

# Public demo: restrict to 20 vessels so we're not publishing the full dataset
ALLOWED_MMSIS: set[int] = set()


@asynccontextmanager
async def lifespan(_: FastAPI):
    global ALLOWED_MMSIS
    sql = "SELECT mmsi FROM ais_202503_static WHERE mmsi IS NOT NULL ORDER BY mmsi LIMIT 20"
    try:
        rows = nq(sql) if NEON_CONN else sq(sql)
        ALLOWED_MMSIS = {r["mmsi"] for r in rows}
    except Exception:
        pass
    yield


app = FastAPI(lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["https://vesselviz.vercel.app"], allow_methods=["*"], allow_headers=["*"])


def nq(sql: str, params: list | None = None) -> list[dict]:
    """Execute SQL via Neon HTTP API."""
    body: dict = {"query": sql}
    if params:
        body["params"] = params
    r = httpx.post(NEON_URL, json=body, headers={"Neon-Connection-String": NEON_CONN}, timeout=60)
    r.raise_for_status()
    return r.json()["rows"]


def sq(sql: str, params: list | None = None) -> list[dict]:
    """Execute SQL via SQLite."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(sql, params or []).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def query(pg_sql: str, lite_sql: str, pg_params: list | None = None, lite_params: list | None = None) -> list[dict]:
    return nq(pg_sql, pg_params) if NEON_CONN else sq(lite_sql, lite_params)


@app.get("/")
def root():
    return {"message": "app is running", "db": "neon" if NEON_CONN else "sqlite"}


@app.get("/api/vessels")
def get_vessels():
    ccg = query(
        pg_sql="""
            SELECT DISTINCT ON (mmsi) mmsi, vessel_name, ship_type, 'CCG' AS source
            FROM ais_202503_static WHERE mmsi IS NOT NULL ORDER BY mmsi
        """,
        lite_sql="""
            SELECT mmsi, vessel_name, ship_type, 'CCG' AS source
            FROM ais_202503_static WHERE mmsi IS NOT NULL GROUP BY mmsi
        """,
    )
    sat = query(
        pg_sql="""
            SELECT DISTINCT ON (mmsi) mmsi, vessel_name, ship_type, 'satellite' AS source
            FROM ais_satellite WHERE mmsi IS NOT NULL ORDER BY mmsi
        """,
        lite_sql="""
            SELECT DISTINCT mmsi, vessel_name, ship_type, 'satellite' AS source
            FROM ais_satellite WHERE mmsi IS NOT NULL
        """,
    )
    seen = set()
    vessels = []
    for row in list(ccg) + list(sat):
        if row["mmsi"] not in seen and row["mmsi"] in ALLOWED_MMSIS:
            seen.add(row["mmsi"])
            vessels.append({
                "mmsi":        row["mmsi"],
                "vessel_name": row["vessel_name"],
                "ship_type":   row["ship_type"],
                "source":      row["source"],
            })
    return {"vessels": vessels, "count": len(vessels)}


@app.get("/api/vessels/area")
def get_vessels_in_area(
    min_lat: float = Query(...),
    max_lat: float = Query(...),
    min_lon: float = Query(...),
    max_lon: float = Query(...),
):
    ccg = query(
        pg_sql="""
            SELECT s.mmsi, s.vessel_name, s.ship_type, 'CCG' AS source
            FROM ais_202503_static s
            WHERE s.mmsi IN (
                SELECT DISTINCT mmsi FROM ais_202503_dynamic
                WHERE latitude BETWEEN $1 AND $2 AND longitude BETWEEN $3 AND $4
            )
        """,
        lite_sql="""
            SELECT s.mmsi, s.vessel_name, s.ship_type, 'CCG' AS source
            FROM ais_202503_static s
            WHERE s.mmsi IN (
                SELECT DISTINCT mmsi FROM ais_202503_dynamic
                WHERE latitude BETWEEN ? AND ? AND longitude BETWEEN ? AND ?
            )
        """,
        pg_params=[min_lat, max_lat, min_lon, max_lon],
        lite_params=[min_lat, max_lat, min_lon, max_lon],
    )
    sat = query(
        pg_sql="""
            SELECT DISTINCT mmsi, vessel_name, ship_type, 'satellite' AS source
            FROM ais_satellite
            WHERE latitude BETWEEN $1 AND $2 AND longitude BETWEEN $3 AND $4
        """,
        lite_sql="""
            SELECT DISTINCT mmsi, vessel_name, ship_type, 'satellite' AS source
            FROM ais_satellite
            WHERE latitude BETWEEN ? AND ? AND longitude BETWEEN ? AND ?
        """,
        pg_params=[min_lat, max_lat, min_lon, max_lon],
        lite_params=[min_lat, max_lat, min_lon, max_lon],
    )
    seen = set()
    vessels = []
    for row in list(ccg) + list(sat):
        if row["mmsi"] not in seen and row["mmsi"] in ALLOWED_MMSIS:
            seen.add(row["mmsi"])
            vessels.append({
                "mmsi":        row["mmsi"],
                "vessel_name": row["vessel_name"],
                "ship_type":   row["ship_type"],
                "source":      row["source"],
            })
    return {"vessels": vessels, "count": len(vessels)}


@app.get("/api/vessel/{mmsi}/route")
def get_vessel_route(
    mmsi: int,
    start: str | None = Query(None),
    end:   str | None = Query(None),
):
    if ALLOWED_MMSIS and mmsi not in ALLOWED_MMSIS:
        raise HTTPException(status_code=404, detail="Vessel not found")
    points = []

    # --- CCG (time stored as unix epoch integer) ---
    pg_sql   = "SELECT time, longitude, latitude, sog, cog FROM ais_202503_dynamic WHERE mmsi = $1"
    lite_sql = "SELECT time, longitude, latitude, sog, cog FROM ais_202503_dynamic WHERE mmsi = ?"
    pg_params: list = [mmsi]
    lite_params: list = [mmsi]

    if start:
        n = len(pg_params) + 1
        pg_sql   += f" AND time >= EXTRACT(EPOCH FROM ${n}::timestamp)::bigint"
        lite_sql += " AND time >= strftime('%s', ?)"
        pg_params.append(start); lite_params.append(start)
    if end:
        n = len(pg_params) + 1
        pg_sql   += f" AND time <= EXTRACT(EPOCH FROM ${n}::timestamp)::bigint"
        lite_sql += " AND time <= strftime('%s', ?)"
        pg_params.append(end); lite_params.append(end)

    pg_sql += " ORDER BY time"
    lite_sql += " ORDER BY time"

    try:
        for r in query(pg_sql, lite_sql, pg_params, lite_params):
            points.append({"time": r["time"], "latitude": r["latitude"],
                           "longitude": r["longitude"], "sog": r["sog"],
                           "cog": r["cog"], "source": "CCG"})
    except Exception:
        pass

    # --- Satellite (time stored as compact ISO string e.g. 20251201T035835Z) ---
    sat_start = start.replace("-", "").replace(":", "").replace(" ", "T") if start else None
    sat_end   = end.replace("-", "").replace(":", "").replace(" ", "T") if end else None

    pg_sat   = "SELECT time, longitude, latitude, sog, cog FROM ais_satellite WHERE mmsi = $1"
    lite_sat = "SELECT time, longitude, latitude, sog, cog FROM ais_satellite WHERE mmsi = ?"
    pg_sat_params: list = [mmsi]
    lite_sat_params: list = [mmsi]

    if sat_start:
        n = len(pg_sat_params) + 1
        pg_sat   += f" AND time >= ${n}"
        lite_sat += " AND time >= ?"
        pg_sat_params.append(sat_start); lite_sat_params.append(sat_start)
    if sat_end:
        n = len(pg_sat_params) + 1
        pg_sat   += f" AND time <= ${n}"
        lite_sat += " AND time <= ?"
        pg_sat_params.append(sat_end); lite_sat_params.append(sat_end)

    pg_sat += " ORDER BY time"
    lite_sat += " ORDER BY time"

    try:
        for r in query(pg_sat, lite_sat, pg_sat_params, lite_sat_params):
            points.append({"time": r["time"], "latitude": r["latitude"],
                           "longitude": r["longitude"], "sog": r["sog"],
                           "cog": r["cog"], "source": "satellite"})
    except Exception:
        pass

    points.sort(key=lambda p: str(p["time"]))
    return {"mmsi": mmsi, "points": points, "count": len(points)}
