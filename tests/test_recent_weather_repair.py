"""Safety regressions for the recent-only workflow repair."""
import ast
from pathlib import Path
from unittest.mock import patch
import pandas as pd
ROOT=Path(__file__).resolve().parents[1]

def test_repair_bounds_and_source_policy():
 s=(ROOT/'scripts/pipeline/repair_recent_weather.py').read_text()
 ast.parse(s)
 assert 'archive-api' not in s
 assert 'days=5' in s and 'LIMIT 40' in s
 assert 'time.monotonic()+600' in s
 assert 'math.isfinite' in s and "assert ov==nv,'finite cell changed'" in s
 assert 'else None' in s
 w=(ROOT/'.github/workflows/daily_pipeline.yml').read_text()
 assert w.index('Auto-repair recent weather gaps')<w.index('Assert written data contract')

def test_feature_stream_retains_exact_filter_and_batches():
 from src.features import load_bulk_clean_data
 class Cursor:
  def __enter__(self):return self
  def __exit__(self,*args):pass
  def execute(self,sql,params):
   assert 'ORDER BY' not in sql
   assert 'datetime_local >= %(cutoff)s' in sql
   assert 'datetime_utc >= %(utc_floor)s' in sql
   assert (params['cutoff']-params['utc_floor']).days==1
   self.n=0
  def fetchmany(self,n):
   assert n==20000
   self.n+=1
   return [(3,'pm25',8.0,pd.Timestamp('2026-10-01'))] if self.n==1 else []
 class Conn:
  def cursor(self,name):assert name=='clean_feature_stream';return Cursor()
  def commit(self):pass
 df=load_bulk_clean_data(Conn(),[3])
 assert len(df)==1 and df.iloc[0]['value']==8.0
