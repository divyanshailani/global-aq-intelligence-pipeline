"""
Global AQ Intelligence — Core Tests
=====================================
Tests for:
  1. Feature leakage prevention (rolling features use shifted values)
  2. Schema alignment (sql/schema.sql matches what scripts expect)
  3. Prediction JSON shape (output format for frontend)
  4. Config module (shared credentials, no hardcoded values)
  5. Model metadata (features list matches training)

Run: python -m pytest tests/ -v
"""

import os
import sys
import json
import re
import glob
from unittest.mock import patch

import pandas as pd
import numpy as np

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

PROJECT_ROOT = os.path.join(os.path.dirname(__file__), "..")


class TestIncrementalArchiveFallback:
    """S3 archive lag must not consume OpenAQ REST API keys by default."""

    def test_archive_lag_does_not_use_rest_fallback(self):
        from scripts.pipeline import run_daily_collector as collector

        with patch.object(collector, "get_gap_days", return_value=7), \
                patch.object(collector, "run_fetch", return_value={"rows_inserted": 0}):
            result = collector.run_incremental(["US"])

        assert result["US"]["source"] == "S3_ARCHIVE_LAG"

    def test_s3_error_does_not_use_rest_fallback(self):
        from scripts.pipeline import run_daily_collector as collector

        with patch.dict(os.environ, {"ALLOW_OPENAQ_API_FALLBACK": "0"}), \
                patch.object(collector, "get_gap_days", return_value=7), \
                patch.object(collector, "run_fetch", side_effect=RuntimeError("S3 unavailable")):
            result = collector.run_incremental(["US"])

        assert result["US"]["source"] == "S3_ERROR"


# ─── Test 1: Rolling Feature Leakage Prevention ──────────────

class TestFeatureLeakage:
    """Verify that rolling features do NOT include today's target value."""

    def test_src_features_uses_shift(self):
        """src/features.py must shift before computing rolling stats."""
        features_path = os.path.join(PROJECT_ROOT, "src", "features.py")
        with open(features_path) as f:
            code = f.read()
        
        # Must contain .shift(1) before rolling computation
        assert ".shift(1)" in code, \
            "src/features.py must use .shift(1) before rolling to prevent leakage"
    
    def test_rolling_excludes_current_day(self):
        """Simulate: rolling mean of [10, 20, 30, 40] with shift should NOT include day 4's value."""
        from src.features import add_rolling_features
        
        df = pd.DataFrame({"pm25": [10, 20, 30, 40, 50]}, 
                          index=pd.date_range("2024-01-01", periods=5))
        result = add_rolling_features(df.copy(), "pm25", windows=[3])
        
        # On day 5 (index 4): roll_3_mean should be mean of days 2,3,4 = (20+30+40)/3 = 30
        # NOT mean of days 3,4,5 = (30+40+50)/3 = 40 (that would be leakage)
        day5_roll = result.iloc[4]["roll_3_mean"]
        assert abs(day5_roll - 30.0) < 0.01, \
            f"roll_3_mean on day 5 should be 30.0 (shifted), got {day5_roll}"

    def test_build_global_fixes_all_three_rolling(self):
        """build_global_features.py must fix roll_3_mean, roll_7_mean, AND roll_3_std."""
        build_path = os.path.join(PROJECT_ROOT, "scripts", "operations", "build_global_features.py")
        with open(build_path) as f:
            code = f.read()
        
        assert "roll_3_mean" in code, "Must fix roll_3_mean"
        assert "roll_7_mean" in code, "Must fix roll_7_mean"
        assert "roll_3_std" in code, "Must fix roll_3_std"


# ─── Test 2: Schema Alignment ────────────────────────────────

class TestSchema:
    """Verify sql/schema.sql includes all v5 columns and tables."""

    def setup_method(self):
        schema_path = os.path.join(PROJECT_ROOT, "sql", "schema.sql")
        with open(schema_path) as f:
            self.schema = f.read().lower()

    def test_daily_features_has_country_code(self):
        assert "country_code" in self.schema

    def test_daily_features_has_nasa_columns(self):
        for col in ["nasa_temperature", "nasa_humidity", "nasa_wind_speed", 
                     "precipitation", "wind_direction"]:
            assert col in self.schema, f"Missing column: {col}"

    def test_daily_features_has_fire_count(self):
        assert "fire_count" in self.schema

    def test_pipeline_runs_table_exists(self):
        assert "pipeline_runs" in self.schema

    def test_prediction_log_table_exists(self):
        assert "prediction_log" in self.schema


# ─── Test 3: Prediction JSON Shape ───────────────────────────

class TestPredictionJSON:
    """Verify prediction output JSON matches what the frontend expects."""

    def test_site_data_json_shape(self):
        """Check existing site_data JSONs have the expected structure."""
        site_data_dir = os.path.join(PROJECT_ROOT, "site_data")
        if not os.path.exists(site_data_dir):
            return  # skip if no data generated yet
        
        json_files = glob.glob(os.path.join(site_data_dir, "predictions_*.json"))
        if not json_files:
            return  # skip if empty
        
        for jf in json_files:
            with open(jf) as f:
                data = json.load(f)
            
            # Must have top-level keys
            assert "country" in data, f"{jf} missing 'country'"
            predictions = data.get("predictions", data.get("forecast"))
            assert predictions is not None, f"{jf} missing 'predictions' or 'forecast'"
            
            # Each prediction must have required fields
            if predictions:
                pred = predictions[0]
                required = ["target_date", "horizon_days", "confidence"]
                for key in required:
                    assert key in pred, f"{jf} prediction missing '{key}'"
                assert any(key in pred for key in ("predicted_pm25", "mean_pm25")), \
                    f"{jf} prediction missing a PM2.5 value"

    def test_publication_contract_is_explicit(self):
        """The pipeline output and workflow publication paths must agree."""
        from src.config import SITE_DATA_DIR

        assert os.path.normpath(SITE_DATA_DIR) == os.path.normpath(os.path.join(PROJECT_ROOT, "site_data"))
        workflow_path = os.path.join(PROJECT_ROOT, ".github", "workflows", "daily_pipeline.yml")
        with open(workflow_path) as f:
            workflow = f.read()
        assert "files=(site_data/*.json)" in workflow
        assert 'cp -f "${files[@]}" "$FRONTEND_DIR/public/data/"' in workflow


# ─── Test 4: Config Module ───────────────────────────────────

class TestConfig:
    """Verify shared config module works correctly."""

    def test_config_imports(self):
        from src.config import DB_CONFIG, MODEL_DIR, SITE_DATA_DIR
        assert "dbname" in DB_CONFIG
        assert "password" in DB_CONFIG
        assert "user" in DB_CONFIG

    def test_config_reads_env_vars(self):
        """Config should use env vars when set."""
        os.environ["POSTGRES_PASSWORD"] = "test_password_12345"
        # Reload module
        import importlib
        from src import config
        importlib.reload(config)
        assert config.DB_CONFIG["password"] == "test_password_12345"
        # Restore to a non-secret placeholder so tests never carry real-looking creds.
        os.environ["POSTGRES_PASSWORD"] = "test_password_restored"
        importlib.reload(config)

    def test_no_hardcoded_creds_in_scripts(self):
        """Tracked code/docs should not carry hardcoded DB passwords or infra IDs."""
        roots = ["scripts", "src", ".github"]
        # acc1 (globalaqiserver) was retired 2026-08-20; do not hardcode its old
        # password fragment here — that would keep a retired secret in the public
        # repo. Guard the live production host instead (a hostname is not secret).
        forbidden = [
            r"globalaqi-archive\.postgres\.database\.azure\.com",
            r"4\.213\.226\.19",
        ]
        violations = []
        for root in roots:
            for path, _, files in os.walk(os.path.join(PROJECT_ROOT, root)):
                if "node_modules" in path:
                    continue
                for filename in files:
                    if not filename.endswith((".py", ".sh", ".yml", ".yaml", ".ipynb", ".md", ".ts", ".tsx")):
                        continue
                    file_path = os.path.join(path, filename)
                    with open(file_path, errors="ignore") as f:
                        content = f.read()
                    if any(re.search(pattern, content) for pattern in forbidden):
                        violations.append(os.path.relpath(file_path, PROJECT_ROOT))
        
        assert not violations, \
            f"Sensitive literals found in: {', '.join(violations)}"


# ─── Test 5: Model Metadata ──────────────────────────────────

class TestModelMetadata:
    """Verify model files have matching metadata."""

    def test_model_meta_files_exist(self):
        """Each .pkl should have a matching _meta.json."""
        model_dir = os.path.join(PROJECT_ROOT, "models", "v12")
        if not os.path.exists(model_dir):
            return  # skip if no models trained yet
        
        pkl_files = glob.glob(os.path.join(model_dir, "*_pm25_gbr.pkl"))
        for pkl in pkl_files:
            meta = pkl.replace("_gbr.pkl", "_meta.json")
            assert os.path.exists(meta), \
                f"Missing metadata for {os.path.basename(pkl)}"

    def test_meta_has_features_list(self):
        """Metadata JSON must contain 'features' key."""
        model_dir = os.path.join(PROJECT_ROOT, "models", "v12")
        if not os.path.exists(model_dir):
            return
        
        meta_files = glob.glob(os.path.join(model_dir, "*_meta.json"))
        for mf in meta_files:
            with open(mf) as f:
                data = json.load(f)
            metadata_entries = data.values() if isinstance(data, dict) and "features" not in data else [data]
            for entry in metadata_entries:
                assert "features" in entry, f"{mf} metadata entry missing 'features' key"
                assert isinstance(entry["features"], list), f"{mf} features must be a list"
                assert len(entry["features"]) > 0, f"{mf} features list is empty"
