import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('t7r_replay_cli', ROOT / 'scripts/battle_replay.py')
cli = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cli)


def test_baseline_freezes_sources_and_replays_from_snapshot(tmp_path):
    output = tmp_path / 'baseline'
    manifest = cli.record(output)
    assert manifest['events'] > 20 and manifest['cases'] >= 3
    assert cli.verify(output)['matched']
    process = subprocess.run([sys.executable, str(output / 'snapshot/scripts/battle_replay.py'),
                              'verify', '--input', str(output)], capture_output=True, text=True)
    assert process.returncode == 0, process.stderr
    assert json.loads(process.stdout)['matched']
    with pytest.raises(FileExistsError):
        cli.record(output)


def test_verification_reports_exact_divergence_instead_of_updating_goldens(tmp_path):
    output = tmp_path / 'baseline'
    cli.record(output)
    path = output / 'expected.json'
    expected = cli.readJson(path)
    expected[0]['steps'][0]['phase'] = 'changed'
    path.write_text(json.dumps(expected), encoding='utf-8')
    with pytest.raises(ValueError, match=r'\$\[0\].steps\[0\].phase'):
        cli.verify(output)


def test_verification_rejects_a_different_reference_source(tmp_path, monkeypatch):
    output = tmp_path / 'baseline'
    cli.record(output)
    monkeypatch.setattr(cli, 'sourceManifest', lambda _: {})
    with pytest.raises(ValueError, match='source mismatch'):
        cli.verify(output)
