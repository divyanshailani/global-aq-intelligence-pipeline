"""Missing launches must be caught outside GitHub, without duplicate dispatch."""
import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('watchdog', ROOT / 'scripts/operations/pipeline_watchdog.py')
w = importlib.util.module_from_spec(spec)
spec.loader.exec_module(w)


def check(at, runs, state=None):
    calls = []
    def sh(argv):
        calls.append(argv)
        return CompletedProcess(argv, 0, json.dumps(runs) if argv[1:3] == ['run', 'list'] else '', '')
    with patch.object(w, 'now_utc', return_value=datetime.fromisoformat(at)), patch.object(w, 'sh', side_effect=sh), patch.object(w, 'load_state', return_value=state or {}), patch.object(w, 'save_state') as save:
        r = w.Report()
        w.check_run_exists(r, True)
        return r, calls, save.call_count


def test_exact_grace_boundary():
    r, _, _ = check('2026-10-16T13:42:59+00:00', [])
    assert not r.dispatched
    r, calls, saved = check('2026-10-16T13:43:00+00:00', [])
    assert r.dispatched and saved == 1
    assert len(calls) == 2


def test_existing_today_or_prior_active_prevents_dispatch():
    for run in [dict(createdAt='2026-10-16T07:00:00Z', databaseId=1, status='completed', conclusion='success'), dict(createdAt='2026-10-15T23:50:00Z', databaseId=2, status='in_progress')]:
        r, calls, _ = check('2026-10-16T14:00:00+00:00', [run])
        assert not r.dispatched and len(calls) == 1


def test_daily_cap():
    r, calls, _ = check('2026-10-16T14:00:00+00:00', [], {'last_dispatch':'2026-10-16'})
    assert not r.dispatched and len(calls) == 1
