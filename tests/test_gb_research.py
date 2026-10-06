import importlib.util
from pathlib import Path
import unittest
import pandas as pd

p=Path(__file__).parents[1]/'research/gb/evaluate.py'
spec=importlib.util.spec_from_file_location('gbe',p);gbe=importlib.util.module_from_spec(spec);spec.loader.exec_module(gbe)
class Tests(unittest.TestCase):
 def test_target_exact_calendar_not_next_row(self):
  d=pd.DataFrame({'station_id':[1,1,1],'date':pd.to_datetime(['2025-01-01','2025-01-03','2025-01-04']),'value':[10,30,40]})
  q=gbe.targets(d,1)
  self.assertEqual(len(q),1);self.assertEqual(q.iloc[0].value,30);self.assertEqual(q.iloc[0].target,40)
 def test_feature_order_matches_champion(self):
  import json
  j=json.loads((Path(__file__).parents[1]/'models/v12/GB/horizon_1/model.json').read_text())
  self.assertEqual(gbe.F,j['learner']['feature_names'])
if __name__=='__main__':unittest.main()
