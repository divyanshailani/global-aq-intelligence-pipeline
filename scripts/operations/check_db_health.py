#!/usr/bin/env python3
"""Read-only production DB health check. No writes."""
import os, re, sys
import psycopg2

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from src.config import DB_CONFIG

conn = psycopg2.connect(**DB_CONFIG)
cur = conn.cursor()
conn.autocommit = True
def one(sql, p=None):
    cur.execute(sql, p or ())
    return cur.fetchone()

print(f"host={DB_CONFIG['host']} db={DB_CONFIG['dbname']}")
print(f"server_time={one('SELECT now()')[0]}")

print("\n=== ROW COUNTS (approx, pg_class) ===")
cur.execute("""
    SELECT relname, n_live_tup FROM pg_stat_user_tables
    WHERE relname IN
    ('raw_measurements','clean_measurements','daily_features','stations','prediction_log')
    ORDER BY n_live_tup DESC
""")
for r in cur.fetchall():
    print(f"{r[0]:22} ~{r[1]:,}")
date_cols = {}
for t in ["raw_measurements", "prediction_log"]:
    cur.execute("""
        SELECT column_name FROM information_schema.columns
        WHERE table_name = %s AND (column_name LIKE '%%date%%' OR column_name LIKE '%%time%%')
    """, (t,))
    date_cols[t] = [c[0] for c in cur.fetchall()]
print(f"raw_measurements date-ish cols: {date_cols['raw_measurements']}")
print(f"prediction_log date-ish cols: {date_cols['prediction_log']}")

for t, col in [("daily_features", "date")] + \
        [(t, date_cols[t][-1] if date_cols[t] else "id") for t in date_cols]:
    try:
        m = one(f"SELECT MAX({col}) FROM {t}")[0]
        d = one(f"SELECT current_date - MAX({col})::date FROM {t}")[0]
        print(f"{t:22} max({col})={m}  ({d} days behind today)")
    except Exception as e:
        print(f"{t:22} ERR {e}")

print("\n=== RECENT DAILY_FEATURES (last 14 days, by date) ===")
cur.execute("""
    SELECT date,
           COUNT(*) AS rows,
           COUNT(DISTINCT station_id) AS stations,
           COUNT(*) FILTER (WHERE om_temperature IS NULL) AS null_temp,
           COUNT(*) FILTER (WHERE om_precipitation IS NULL) AS null_precip,
           COUNT(*) FILTER (WHERE om_aerosol_optical_depth IS NULL) AS null_aod,
           COUNT(*) FILTER (WHERE country_code IS NULL) AS null_cc,
           COUNT(*) FILTER (WHERE rolling_3day_precip IS NULL) AS null_roll
    FROM daily_features
    WHERE date >= current_date - 14
    GROUP BY date ORDER BY date
""")
print(f"{'date':12} {'rows':>8} {'st':>5} {'n_temp':>7} {'n_prec':>7} {'n_aod':>7} {'n_cc':>6} {'n_roll':>7}")
for r in cur.fetchall():
    print(f"{str(r[0]):12} {r[1]:>8} {r[2]:>5} {r[3]:>7} {r[4]:>7} {r[5]:>7} {r[6]:>6} {r[7]:>7}")

print("\n=== NULL RATES over last 90 days ===")
cur.execute("""
    SELECT COUNT(*),
           COUNT(*) FILTER (WHERE om_temperature IS NULL),
           COUNT(*) FILTER (WHERE om_precipitation IS NULL),
           COUNT(*) FILTER (WHERE om_aerosol_optical_depth IS NULL),
           COUNT(*) FILTER (WHERE rolling_3day_precip IS NULL)
    FROM daily_features WHERE date >= current_date - 90
""")
r = cur.fetchone()
print(f"total={r[0]:,}  null_temp={r[1]:,} ({100*r[1]/r[0]:.1f}%)  "
      f"null_precip={r[2]:,} ({100*r[2]/r[0]:.1f}%)  null_aod={r[3]:,} ({100*r[3]/r[0]:.1f}%)  "
      f"null_roll={r[4]:,} ({100*r[4]/r[0]:.1f}%)")

print("\n=== NULL RATES over full history ===")
cur.execute("""
    SELECT COUNT(*),
           COUNT(*) FILTER (WHERE om_temperature IS NULL),
           COUNT(*) FILTER (WHERE om_aerosol_optical_depth IS NULL)
    FROM daily_features
""")
r = cur.fetchone()
print(f"total={r[0]:,}  null_temp={r[1]:,} ({100*r[1]/r[0]:.1f}%)  null_aod={r[2]:,} ({100*r[2]/r[0]:.1f}%)")

print("\n=== COUNTRY SPLIT (last 30 days) ===")
cur.execute("""
    SELECT country_code, COUNT(*), COUNT(DISTINCT station_id)
    FROM daily_features WHERE date >= current_date - 30
    GROUP BY country_code ORDER BY country_code
""")
for r in cur.fetchall():
    print(f"{str(r[0]):6} rows={r[1]:>8}  stations={r[2]}")

print("\n=== PREDICTION LOG (last 6) ===")
cur.execute("""
    SELECT created_at, country_code, horizon_days, live_mae, test_mae, drift_flag
    FROM prediction_log ORDER BY created_at DESC LIMIT 6
""")
try:
    for r in cur.fetchall():
        print(f"{str(r[0])[:16]} {str(r[1]):4} h={r[2]:>2} live_mae={r[3]} test_mae={r[4]} drift={r[5]}")
except Exception as e:
    cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name='prediction_log'")
    print("cols:", [c[0] for c in cur.fetchall()])

print("\n=== TABLE SIZES ===")
cur.execute("""
    SELECT relname, pg_size_pretty(pg_total_relation_size(oid))
    FROM pg_class WHERE relname IN
    ('raw_measurements','clean_measurements','daily_features','stations','prediction_log')
    ORDER BY pg_total_relation_size(oid) DESC
""")
for r in cur.fetchall():
    print(f"{r[0]:22} {r[1]}")

conn.close()
print("\nDONE")
