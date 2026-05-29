"""
One-time script: permanently delete all but the 20 demo vessels.
Run once on Railway console or locally with NEON_CONNECTION_STRING set.

    python trim_to_demo.py
"""
import os
import re
import sqlite3
from pathlib import Path

import httpx

DB_PATH = Path(__file__).parent / "data" / "ais.db"
NEON_CONN: str = os.environ.get("NEON_CONNECTION_STRING", "")
_neon_host_match = re.search(r'@([^/?]+)', NEON_CONN)
NEON_URL = f"https://{_neon_host_match.group(1)}/sql" if _neon_host_match else ""


def nq(sql: str, params: list | None = None) -> list[dict]:
    body: dict = {"query": sql}
    if params:
        body["params"] = params
    r = httpx.post(NEON_URL, json=body, headers={"Neon-Connection-String": NEON_CONN}, timeout=60)
    r.raise_for_status()
    return r.json().get("rows", [])


def sq(sql: str, params: list | None = None) -> list[dict]:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(sql, params or []).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def run_write(sql: str, params: list | None = None) -> int:
    if NEON_CONN:
        body: dict = {"query": sql}
        if params:
            body["params"] = params
        r = httpx.post(NEON_URL, json=body, headers={"Neon-Connection-String": NEON_CONN}, timeout=120)
        r.raise_for_status()
        return r.json().get("rowCount", 0)
    else:
        conn = sqlite3.connect(str(DB_PATH))
        cur = conn.execute(sql, params or [])
        conn.commit()
        affected = cur.rowcount
        conn.close()
        return affected


def main():
    # Step 1: pick the 20 demo MMSIs
    rows = nq("SELECT mmsi FROM ais_202503_static WHERE mmsi IS NOT NULL ORDER BY mmsi LIMIT 20") \
        if NEON_CONN else sq("SELECT mmsi FROM ais_202503_static WHERE mmsi IS NOT NULL ORDER BY mmsi LIMIT 20")

    if not rows:
        print("ERROR: no MMSIs found — is the DB reachable?")
        return

    demo_mmsis = [r["mmsi"] for r in rows]
    placeholders = ", ".join(f"${i+1}" for i in range(len(demo_mmsis))) if NEON_CONN \
        else ", ".join("?" for _ in demo_mmsis)

    print(f"Keeping {len(demo_mmsis)} demo vessels: {demo_mmsis}")
    print()

    # Step 2: delete everything else from all three tables
    tables = ["ais_202503_dynamic", "ais_202503_static", "ais_satellite"]
    for table in tables:
        sql = f"DELETE FROM {table} WHERE mmsi NOT IN ({placeholders})"
        deleted = run_write(sql, demo_mmsis)
        print(f"  {table}: deleted {deleted} rows")

    print()
    print("Done. Only demo vessels remain.")


if __name__ == "__main__":
    main()
