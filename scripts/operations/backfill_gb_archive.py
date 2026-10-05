"""Frozen GB archive import. Raw rows and cursor commit in the same transaction."""
from contextlib import closing
import concurrent.futures
import csv
import gzip
import hashlib
import io
import json
import math
import os
from pathlib import Path
import signal
import sys
import time
from datetime import datetime

import psycopg2
from psycopg2.extras import execute_values, Json
import requests

DATA = Path(__file__).with_name('gb_backfill_data')
TASK_ID = 'gb-current-fleet-pre2026-v1'
CHUNK = 100
SLICE_SECONDS = 900
CHUNK_SECONDS = 180


class Deadline(Exception):
    pass


class Yield(Exception):
    pass


def connect():
    return psycopg2.connect(
        host=os.environ['POSTGRES_HOST'], port=os.environ.get('POSTGRES_PORT') or '5432',
        user=os.environ['POSTGRES_USER'], password=os.environ['POSTGRES_PASSWORD'],
        dbname=os.environ['POSTGRES_DB'], sslmode='require', connect_timeout=10,
        keepalives=1, keepalives_idle=20, keepalives_interval=10,
        keepalives_count=3, tcp_user_timeout=60000)


def load_manifest():
    raw = gzip.decompress((DATA / 'manifest.json.gz').read_bytes())
    seed = json.loads((DATA / 'seed.json').read_text())
    if hashlib.sha256(raw).hexdigest() != seed['manifest_sha256']:
        raise ValueError('manifest hash mismatch')
    tasks = json.loads(raw)
    if len(tasks) != seed['total'] or any(t[2] >= '20260101' for t in tasks):
        raise ValueError('manifest scope mismatch')
    if tasks != sorted(tasks, key=lambda t: t[2], reverse=True):
        raise ValueError('manifest order mismatch')
    return tasks, seed


def pipeline_busy():
    """Shared Actions concurrency prevents overlap; API check gives queued daily priority."""
    url = f"https://api.github.com/repos/{os.environ['GITHUB_REPOSITORY']}/actions/workflows/daily_pipeline.yml/runs"
    for status in ('queued', 'in_progress', 'waiting', 'pending'):
        r = requests.get(url, params={'status': status, 'per_page': 1},
                         headers={'Authorization': f"Bearer {os.environ['GH_TOKEN']}"}, timeout=15)
        r.raise_for_status()
        if r.json()['total_count']:
            return True
    return False


def load_state(seed, initialize):
    with closing(connect()) as c, c:
        with c.cursor() as cur:
            cur.execute("SELECT to_regclass('public.backfill_state')")
            exists = cur.fetchone()[0] is not None
            if not exists and not initialize:
                return None
            if initialize:
                cur.execute('''CREATE TABLE IF NOT EXISTS backfill_state (
                    task_id text PRIMARY KEY, manifest_sha256 text NOT NULL,
                    state jsonb NOT NULL, updated_at timestamptz NOT NULL DEFAULT now())''')
                state = dict(seed, finished=False, skipped_rows=0, run_id=None,
                             banked_rows=seed['inserted_rows'] + seed.get('legacy_recovered_rows', 0))
                cur.execute('''INSERT INTO backfill_state(task_id,manifest_sha256,state)
                    VALUES (%s,%s,%s) ON CONFLICT(task_id) DO NOTHING''',
                            (TASK_ID, seed['manifest_sha256'], Json(state)))
            cur.execute('SELECT manifest_sha256,state FROM backfill_state WHERE task_id=%s', (TASK_ID,))
            row = cur.fetchone()
            if row is None:
                return None
            if row[0] != seed['manifest_sha256']:
                raise ValueError('DB checkpoint uses a different manifest')
            return row[1]


def parse_file(task, content):
    sid, oid, day, key = task
    rows, skipped = [], 0
    for row in csv.DictReader(io.StringIO(gzip.decompress(content).decode('utf-8'))):
        # A changed archive schema is a hard error, not an empty successful file.
        for field in ('value', 'sensors_id', 'parameter', 'units', 'datetime'):
            if field not in row:
                raise ValueError(f'missing archive field {field}: {key}')
        try:
            value = float(row['value'])
            sensor = int(row['sensors_id'])
            timestamp = datetime.fromisoformat(row['datetime'].replace('Z', '+00:00'))
            if not math.isfinite(value) or value < 0 or timestamp.tzinfo is None:
                raise ValueError('invalid value or timestamp')
        except (ValueError, TypeError):
            skipped += 1
            continue
        rows.append((sid, sensor, row['parameter'], value, row['units'],
                     row['datetime'], row['datetime']))
    return rows, skipped, hashlib.sha256(content).hexdigest()


def fetch(task):
    for attempt in range(3):
        try:
            r = requests.get('https://openaq-data-archive.s3.amazonaws.com/' + task[3], timeout=(10, 25))
            r.raise_for_status()
            return parse_file(task, r.content)
        except (requests.RequestException, OSError):
            if attempt == 2:
                raise
            time.sleep(2 ** attempt)


def flush(seed, expected, chunk, results):
    if pipeline_busy():
        raise Yield('daily pipeline queued or running')
    c = connect()
    try:
        with c:
            with c.cursor() as cur:
                cur.execute("SET LOCAL statement_timeout='90s'")
                cur.execute("SET LOCAL lock_timeout='5s'")
                cur.execute('SELECT pg_database_size(current_database())')
                if cur.fetchone()[0] >= 28 * 1024 ** 3:
                    raise RuntimeError('28 GiB database size guard')
                cur.execute("SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() AND state='active' AND pid<>pg_backend_pid() AND query NOT ILIKE 'SELECT%'")
                if cur.fetchone()[0]:
                    raise Yield('another writer is active')
                cur.execute('SELECT state FROM backfill_state WHERE task_id=%s FOR UPDATE', (TASK_ID,))
                state = cur.fetchone()[0]
                if state['processed'] != expected:
                    return state  # Another continuation already committed this chunk.
                inserted = 0
                for rows, skipped, digest in results:
                    for off in range(0, len(rows), 1000):
                        execute_values(cur, '''INSERT INTO raw_measurements
                            (station_id,sensor_id,parameter,value,unit,datetime_utc,datetime_local)
                            VALUES %s ON CONFLICT(station_id,parameter,datetime_utc) DO NOTHING''',
                                       rows[off:off+1000], page_size=1000)
                        inserted += max(cur.rowcount, 0)
                state['processed'] += len(chunk)
                state['inserted_rows'] += inserted
                state['banked_rows'] = state['inserted_rows'] + state.get('legacy_recovered_rows', 0)
                state['fetched_rows'] += sum(len(r[0]) for r in results)
                state['skipped_rows'] += sum(r[1] for r in results)
                for task in chunk:
                    year = task[2][:4]
                    state['per_year'][year] = state['per_year'].get(year, 0) + 1
                state['finished'] = state['processed'] == state['total']
                state['run_id'] = os.environ.get('GITHUB_RUN_ID')
                state['last_chunk_hash'] = hashlib.sha256(json.dumps(
                    [(t[3], r[2]) for t, r in zip(chunk, results)]).encode()).hexdigest()
                # Atomic with raw writes: even loss of the COMMIT response is safe to replay.
                cur.execute('UPDATE backfill_state SET state=%s,updated_at=now() WHERE task_id=%s',
                            (Json(state), TASK_ID))
        return state
    finally:
        c.close()


def output(status):
    if os.environ.get('GITHUB_OUTPUT'):
        with open(os.environ['GITHUB_OUTPUT'], 'a') as f:
            f.write(f'status={status}\n')
    print('RESULT', status, flush=True)


def main():
    tasks, seed = load_manifest()
    initialize = os.environ.get('INITIALIZE', 'false').lower() == 'true'
    state = load_state(seed, initialize)
    if state is None or state['finished']:
        output('done' if state else 'inactive')
        return
    started = time.monotonic()
    signal.signal(signal.SIGALRM, lambda *args: (_ for _ in ()).throw(Deadline()))
    while state['processed'] < len(tasks):
        if time.monotonic() - started > SLICE_SECONDS - CHUNK_SECONDS:
            output('continue')
            return
        if pipeline_busy():
            output('daily_first')
            return
        chunk = tasks[state['processed']:state['processed'] + CHUNK]
        signal.alarm(CHUNK_SECONDS)
        pool = concurrent.futures.ThreadPoolExecutor(max_workers=20)
        try:
            results = list(pool.map(fetch, chunk))  # No DB connection exists during fetch.
            pool.shutdown(wait=True)
            state = flush(seed, state['processed'], chunk, results)
            print(json.dumps(state), flush=True)
        except Yield:
            output('daily_first')
            return
        except Deadline:
            # Process exit ends outstanding HTTP threads; DB transaction rolls back if uncommitted.
            output('continue')
            os._exit(0)
        finally:
            signal.alarm(0)
            pool.shutdown(wait=False, cancel_futures=True)
    output('done')


if __name__ == '__main__':
    main()
