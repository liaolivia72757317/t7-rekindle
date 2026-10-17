from pathlib import Path
from types import SimpleNamespace
import sys

import pytest

from scripts import build, integration_test


@pytest.mark.parametrize("configuration", ["Debug", "Release"])
@pytest.mark.parametrize("exit_code", [0, 1])
def test_integration_uses_selected_artifacts_and_restores_build_state(
        tmp_path, monkeypatch, configuration, exit_code):
    root = tmp_path / "repository"
    artifacts = root / "artifacts" / "isolated"
    host = artifacts / "native/bin/x64" / configuration / "T7R.RuntimeTests.exe"
    host.parent.mkdir(parents=True)
    host.touch()
    fixture = artifacts / "tmp/fixture"
    original_root, original_artifacts = build.ROOT, build.ARTIFACTS
    cleaned = []
    caches = []

    def prepare_fixture(python_home):
        assert python_home == tmp_path / "python"
        assert build.ROOT == root and build.artifact_root() == artifacts
        return fixture

    def run_host(command, **options):
        assert command == [str(host), str(fixture)]
        assert options["cwd"] == root
        assert options["env"]["LOCALAPPDATA"] == str(fixture / "state")
        return SimpleNamespace(returncode=exit_code, stderr="host failed", stdout="")

    class Runtime:
        def __init__(self, scripts, cache):
            assert scripts == root / "tests/reference/Business/scripts"
            caches.append(cache)

        def create(self, context):
            return {}

        def dispatch(self, event, state, context):
            timers = [("1:version",)] if event["type"] == "authenticated" else []
            return state, [], timers, []

        def close(self):
            pass

    monkeypatch.setitem(sys.modules, "build", build)
    monkeypatch.setitem(sys.modules, "host_runtime", SimpleNamespace(Runtime=Runtime, encode=lambda value: value))
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.setattr(build, "check_python", lambda: tmp_path / "python")
    monkeypatch.setattr(build, "prepare_runtime_fixture", prepare_fixture)
    monkeypatch.setattr(build, "remove_tree", cleaned.append)
    monkeypatch.setattr(integration_test.subprocess, "run", run_host)

    if exit_code:
        with pytest.raises(RuntimeError, match="host failed"):
            integration_test.run(root, artifacts=artifacts, configuration=configuration)
        assert not caches
    else:
        report = integration_test.run(root, artifacts=artifacts, configuration=configuration)
        assert report["result"] == "passed" and report["realClient"] is False
        assert Path(report["host"]) == host.relative_to(root)
        assert caches == [artifacts / "tmp/integration-cache"]
    assert cleaned == [fixture]
    assert build.ROOT == original_root and build.ARTIFACTS == original_artifacts
