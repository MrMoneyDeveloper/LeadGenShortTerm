import subprocess
from pathlib import Path


def test_drive_checksum_and_resume_state():
    root = Path(__file__).resolve().parents[3]
    harness = root / "tests" / "apps_script_harness.cjs"
    result = subprocess.run(["node", str(harness)], cwd=root, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "apps-script-backup-tests: pass"
