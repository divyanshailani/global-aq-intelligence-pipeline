"""The public PM2.5 models must never receive PM10 or stale station rows."""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def inference_query():
    tree = ast.parse((ROOT / 'scripts/pipeline/predict_v12_onnx.py').read_text())
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
              and n.name == 'load_latest_features_from_db')
    assignment = next(n for n in fn.body if isinstance(n, ast.Assign)
                      and any(isinstance(t, ast.Name) and t.id == 'query' for t in n.targets))
    return ast.literal_eval(assignment.value)


def test_pm25_filter_on_both_anchor_and_model_inputs():
    sql = inference_query()
    assert "WHERE parameter = 'pm25'" in sql
    assert "WHERE df.parameter = 'pm25'" in sql
    assert "country_code IN ('AU', 'GB', 'IN', 'US')" in sql


def test_station_dates_match_country_pm25_anchor_not_individual_old_dates():
    sql = inference_query()
    assert 'MAX(date) AS date' in sql
    assert 'GROUP BY country_code' in sql
    assert 'df.country_code = latest.country_code AND df.date = latest.date' in sql
    assert 'JOIN stations s ON df.station_id = s.id' in sql
