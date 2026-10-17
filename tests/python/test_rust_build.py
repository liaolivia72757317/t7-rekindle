from pathlib import Path
from unittest.mock import patch
from xml.etree import ElementTree

import pytest

from test_build import load_build


@pytest.mark.parametrize('project', ['T7R.RustBridgeTests.vcxproj', 'T7R.RustGatewayFixture.vcxproj'])
def test_native_rust_tests_link_from_the_selected_artifact_root(project):
    root = Path(__file__).resolve().parents[2]
    tree = ElementTree.parse(root / 'tests/cpp' / project)
    directories = tree.find('.//{*}AdditionalLibraryDirectories').text
    assert directories.startswith('$(T7RArtifactsRoot)rust\\')


@pytest.mark.parametrize('project', ['rust', 'rust-tests', 'rust-bridge-tests', 'rust-gateway-tests'])
@pytest.mark.parametrize('configuration', ['Debug', 'Release'])
def test_rust_targets_do_not_require_python_or_managed_toolchains(monkeypatch, project, configuration):
    build = load_build()
    monkeypatch.setattr(build.sys, 'argv', ['build.py', '--project', project, '--configuration', configuration])
    with (patch.object(build, 'run_rust') as run,
          patch.object(build, 'check_python') as python,
          patch.object(build, 'check_managed_sdk') as managed):
        build.main()
    run.assert_called_once_with(project, configuration)
    python.assert_not_called()
    managed.assert_not_called()


@pytest.mark.parametrize('configuration,release', [('Debug', False), ('Release', True)])
def test_cargo_is_locked_and_uses_only_its_own_output_directory(configuration, release):
    build = load_build()
    with patch.object(build.shutil, 'which', return_value='cargo'), patch.object(build.subprocess, 'run') as run:
        build.run_rust('rust-tests', configuration)
    commands = [call.args[0] for call in run.call_args_list]
    assert [command[1] for command in commands] == ['fmt', 'clippy', 'test']
    assert '--check' in commands[0]
    for command in commands[1:]:
        assert '--locked' in command
        assert ('--release' in command) == release
    for call in run.call_args_list:
        assert call.kwargs['cwd'] == build.ROOT / 'src/BattleRust'
        assert Path(call.kwargs['env']['CARGO_TARGET_DIR']) == build.ROOT / 'artifacts/rust'


def test_missing_cargo_is_reported_without_installing_tools():
    build = load_build()
    with patch.object(build.shutil, 'which', return_value=None), patch.object(build.subprocess, 'run') as run:
        with pytest.raises(RuntimeError, match='Cargo'):
            build.run_rust('rust', 'Release')
    run.assert_not_called()


def test_gateway_target_links_a_test_only_dll_and_runs_the_managed_socket_path():
    build = load_build()
    with (patch.object(build.shutil, 'which', return_value='cargo'),
          patch.object(build.subprocess, 'run') as run,
          patch.object(build, 'locate_msbuild', return_value='msbuild'),
          patch.object(build, 'check_managed_sdk') as managed,
          patch.object(build, 'run_msbuild') as msbuild,
          patch.object(build, 'check_python') as python):
        build.run_rust('rust-gateway-tests', 'Debug')
    managed.assert_called_once_with('msbuild')
    python.assert_not_called()
    assert [call.args[0].name for call in msbuild.call_args_list] == [
        'T7R.RustGatewayFixture.vcxproj', 'T7R.BattleHarness.csproj']
    command = run.call_args_list[-2].args[0]
    assert command[1] == '--rust-gateway'
    assert Path(command[2]).name == 'T7R.RustGatewayFixture.dll'
    assert 'Debug' in command[0] and 'Debug' in command[2]
