import gzip
import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import patch, MagicMock

P = Path(__file__).parents[1] / 'scripts/operations/backfill_gb_archive.py'
spec = importlib.util.spec_from_file_location('gb', P)
gb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gb)


class Tests(unittest.TestCase):
    def test_manifest_and_bootstrap(self):
        tasks, seed = gb.load_manifest()
        self.assertEqual(len(tasks), 123537)
        self.assertEqual(seed['processed'], 2000)
        self.assertEqual(seed['inserted_rows'], 192681)
        self.assertEqual(len({t[0] for t in tasks}), 166)
        self.assertTrue(all(t[2] < '20260101' for t in tasks))

    def test_invalid_values_not_zeroed(self):
        data = 'value,sensors_id,parameter,units,datetime\n'
        for value in ('5', 'NaN', 'inf', '-1', '0'):
            data += f'{value},7,pm25,ug/m3,2025-12-01T00:00:00Z\n'
        rows, skipped, digest = gb.parse_file((1, 2, '20251201', 'file'), gzip.compress(data.encode()))
        self.assertEqual([r[3] for r in rows], [5, 0])
        self.assertEqual(skipped, 3)
        self.assertEqual(len(digest), 64)

    def test_changed_schema_fails(self):
        with self.assertRaises(ValueError):
            gb.parse_file((1, 2, '', 'file'), gzip.compress(b'bad\nx\n'))

    @patch.object(gb, 'pipeline_busy', return_value=True)
    @patch.object(gb, 'connect')
    def test_yield_before_connection(self, connect, busy):
        with self.assertRaises(gb.Yield):
            gb.flush({}, 2000, [], [])
        connect.assert_not_called()

    @patch.object(gb, 'load_state', return_value=None)
    @patch.object(gb, 'connect')
    def test_completion_event_does_not_initialize(self, connect, state):
        with patch.dict(gb.os.environ, {'INITIALIZE': 'false'}):
            gb.main()
        self.assertFalse(state.call_args[0][1])
        connect.assert_not_called()

    @patch.object(gb, 'pipeline_busy', return_value=False)
    @patch.object(gb, 'connect')
    @patch.object(gb, 'execute_values')
    def test_raw_and_checkpoint_share_transaction(self, execute, connect, busy):
        c = MagicMock(); cur = MagicMock(); connect.return_value = c
        c.cursor.return_value.__enter__.return_value = cur
        state = {'processed': 2000, 'total': 2001, 'inserted_rows': 192681,
                 'fetched_rows': 192681, 'skipped_rows': 0, 'per_year': {'2025': 2000}}
        cur.fetchone.side_effect = [(1,), (0,), (state,)]
        cur.rowcount = 1
        out = gb.flush({}, 2000, [(1, 2, '20250101', 'key')],
                       [([(1, 1, 'pm25', 5, 'ug/m3', '2025-01-01T00:00:00Z', '2025-01-01T00:00:00Z')], 0, 'hash')])
        self.assertTrue(out['finished'])
        self.assertEqual(out['inserted_rows'], 192682)
        self.assertTrue(any('UPDATE backfill_state' in call.args[0] for call in cur.execute.call_args_list))
        c.commit.assert_not_called()  # Connection context commits both together.
        c.close.assert_called_once()

    @patch.object(gb, 'pipeline_busy', return_value=False)
    @patch.object(gb, 'connect')
    @patch.object(gb, 'execute_values', side_effect=gb.psycopg2.OperationalError('drop'))
    def test_failed_insert_never_updates_checkpoint(self, execute, connect, busy):
        c = MagicMock(); cur = MagicMock(); connect.return_value = c
        c.cursor.return_value.__enter__.return_value = cur
        cur.fetchone.side_effect = [(1,), (0,), ({'processed': 2000},)]
        with self.assertRaises(gb.psycopg2.OperationalError):
            gb.flush({}, 2000, [(1, 2, '20250101', 'key')],
                     [([(1, 1, 'pm25', 5, 'ug/m3', '2025-01-01T00:00:00Z', '2025-01-01T00:00:00Z')], 0, 'hash')])
        self.assertFalse(any('UPDATE backfill_state' in call.args[0] for call in cur.execute.call_args_list))
        c.close.assert_called_once()


if __name__ == '__main__':
    unittest.main()
