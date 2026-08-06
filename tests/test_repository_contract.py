"""Static guards for repository ownership and publication contracts."""

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_daily_entrypoints_exist_and_are_documented():
    entrypoints = [
        "scripts/run_daily_collector.py",
        "scripts/run_daily_etl.py",
        "scripts/predict_v12_onnx.py",
        "scripts/validate_predictions.py",
    ]
    inventory = (ROOT / "SCRIPT_INVENTORY.md").read_text()
    for relative in entrypoints:
        assert (ROOT / relative).is_file(), relative
        assert relative in inventory, relative


def test_v12_model_grid_is_present():
    model_root = ROOT / "models" / "v12"
    assert model_root.is_dir()
    assert len(list(model_root.rglob("*.onnx"))) >= 1


def test_workflow_publishes_root_site_data_to_frontend_data():
    workflow = (ROOT / ".github" / "workflows" / "daily_pipeline.yml").read_text()
    assert "files=(site_data/*.json)" in workflow
    assert 'mkdir -p "$FRONTEND_DIR/public/data"' in workflow
    assert 'cp -f "${files[@]}" "$FRONTEND_DIR/public/data/"' in workflow


def test_scheduler_ownership_is_explicit():
    project_map = (ROOT / "PROJECT_MAP.md").read_text()
    inventory = (ROOT / "SCRIPT_INVENTORY.md").read_text()
    for path in (
        ".github/workflows/daily_pipeline.yml",
        "scripts/run_cron_local.sh",
        "scripts/run_cron.sh",
        "scripts/admin_dashboard.py",
    ):
        assert path in project_map or path in inventory, path
    assert "No scheduler is disabled" in project_map


def test_python_and_shell_inventory_covers_tracked_scripts():
    inventory = (ROOT / "SCRIPT_INVENTORY.md").read_text()
    tracked = __import__("subprocess").check_output(
        ["git", "ls-files", "*.py", "*.sh"], cwd=ROOT, text=True
    ).splitlines()
    missing = [path for path in tracked if path not in inventory]
    assert not missing, f"Unclassified tracked scripts: {missing}"


def test_no_workflow_points_at_old_site_data_path():
    for workflow_path in (ROOT / ".github" / "workflows").glob("*.y*ml"):
        text = workflow_path.read_text()
        assert not re.search(r"data/site_data", text), workflow_path