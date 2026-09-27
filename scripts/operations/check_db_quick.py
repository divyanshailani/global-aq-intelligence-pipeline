#!/usr/bin/env python3
"""Targeted quick queries: prediction_log schema+rows, stations count, table sizes."""
import os, sys
import psycopg2
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from src.config import DB_CONFIG

conn = psycopg2.connect(**DB_CONFIG)
conn.autocommit = True
cur = conn.cursor()

cur.execute("SELECT COUNT(*) FROM stations"); print(f"stations count = {cur.fetchone()[0]:,}")
cur.execute("SELECT COUNT(*) FROM stations WHERE latitude IS NOT NULL AND longitude IS NOT NULL")
print(f"stations with coords = {cur.fetchone()[0]:,}")

cur.execute("""SELECT column_name, data_type FROM information_schema.columns
               WHERE table_name='prediction_log' ORDER BY ordinal_position""")
print("\nprediction_log columns:", [f"{c[0]}:{c[1]}" for c in cur.fetchall()])

cur.execute("SELECT * FROM prediction_log ORDER BY validated_at DESC NULLS LAST LIMIT 6")
cols = [d[0] for d in cur.description]
print("\nlast 6 prediction_log rows:")
for r in cur.fetchall():
    print("  " + "  ".join(f"{k}={v}" for k, v in zip(cols, r) if v is not None and k not in ('prediction',)))

cur.execute("""SELECT relname, pg_size_pretty(pg_total_relation_size(oid))
    FROM pg_class WHERE relname IN
    ('raw_measurements','clean_measurements','daily_features','stations','prediction_log')
    ORDER BY pg_total_relation_size(oid) DESC""")
print("\ntable sizes:")
for r in cur.fetchall():
    print(f"  {r[0]:22} {r[1]}")

cur.execute("SELECT pg_size_pretty(pg_database_size('indiaaq'))"); print(f"\ndb total = {cur.fetchone()[0]}")

conn.close()
print("\nDONE")
