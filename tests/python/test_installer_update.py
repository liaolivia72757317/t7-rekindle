"""Exercise update installation and launcher restart using disposable fixtures."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

import pytest

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows installer integration")


@pytest.fixture(scope="module")
def update_installer(tmp_path_factory):
    candidates = [
        shutil.which("ISCC.exe"),
        ROOT / ".local/toolchains/InnoSetup-7.1.0/ISCC.exe",
        Path(os.environ.get("RUNNER_TEMP", ".")) / "InnoSetup/ISCC.exe",
    ]
    compiler = next((Path(path) for path in candidates if path and Path(path).is_file()), None)
    if compiler is None:
        pytest.skip("Inno Setup compiler is not installed")
    csharp_compiler = Path(os.environ["WINDIR"]) / "Microsoft.NET/Framework64/v4.0.30319/csc.exe"
    if not csharp_compiler.is_file():
        pytest.skip(".NET Framework C# compiler is not installed")
    work = tmp_path_factory.mktemp("update-installer")
    payload = work / "payload"
    payload.mkdir()
    (payload / "payload.txt").write_text("new release", encoding="utf-8")
    (payload / "added.txt").write_text("new file", encoding="utf-8")
    launcher_source = work / "Launcher.cs"
    launcher_source.write_text(
        'using System;\nusing System.IO;\n'
        'class Launcher { static void Main() {\n'
        '    string directory = AppDomain.CurrentDomain.BaseDirectory;\n'
        '    File.AppendAllText(Path.Combine(directory, "launches.txt"),\n'
        '        File.ReadAllText(Path.Combine(directory, "payload.txt")) + Environment.NewLine);\n'
        '} }\n', encoding="utf-8",
    )
    compiled_launcher = subprocess.run(
        [str(csharp_compiler), "/nologo", "/warnaserror+", "/target:winexe",
         f'/out:{payload / "T7-Rekindle.exe"}', str(launcher_source)],
        capture_output=True, timeout=30,
    )
    assert compiled_launcher.returncode == 0, compiled_launcher.stdout + compiled_launcher.stderr
    source = (ROOT / "installer/T7-Rekindle.iss").read_text(encoding="utf-8")
    run_entries = source.split("[Run]", 1)[1].split("[Code]", 1)[0]
    code = source.split("[Code]", 1)[1]
    script = work / "fixture.iss"
    script.write_text(
        '#define MyAppExeName "T7-Rekindle.exe"\n'
        '[Setup]\nAppId=T7.Update.Installation.Test\nAppName=T7 update test\nAppVersion=1.0\n'
        'DefaultDirName={tmp}\\T7-update-test\nPrivilegesRequired=lowest\nUninstallable=no\n'
        'DisableProgramGroupPage=yes\nOutputBaseFilename=Setup\n'
        f'OutputDir={work}\n[Files]\nSource: "{payload}\\*"; DestDir: "{{app}}"; Flags: ignoreversion\n'
        f'[Run]\n{run_entries}\n[Code]\n{code}', encoding="utf-8-sig",
    )
    compiled = subprocess.run([str(compiler), "/Q", str(script)], capture_output=True, timeout=60)
    assert compiled.returncode == 0, compiled.stdout + compiled.stderr
    assert b"warning" not in (compiled.stdout + compiled.stderr).lower()
    return work / "Setup.exe"


def _target(work):
    target = work / "current launcher 重燃"
    target.mkdir()
    (target / "payload.txt").write_text("old release", encoding="utf-8")
    (target / "legacy.txt").write_text("keep", encoding="utf-8")
    return target


def _start_installer(installer, target, launcher_pid):
    log = target.parent / "install.log"
    # Match the launcher's quoted directory, including its trailing separator.
    command = (f'"{installer}" /VERYSILENT /SP- /SUPPRESSMSGBOXES /NORESTART /NORESTARTAPPLICATIONS '
               f'/DIR="{target}{os.sep}" /LAUNCHERPID={launcher_pid} /LOG="{log}"')
    return subprocess.Popen(command, creationflags=subprocess.CREATE_NO_WINDOW), log


def _start_launcher():
    return subprocess.Popen([sys.executable, "-c", "import sys; sys.stdin.read()"],
                            stdin=subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW)


def _assert_unchanged(target):
    assert (target / "payload.txt").read_text(encoding="utf-8") == "old release"
    assert not (target / "added.txt").exists()
    assert not (target / "launches.txt").exists()


def _assert_installed(target):
    assert (target / "payload.txt").read_text(encoding="utf-8") == "new release"
    assert (target / "added.txt").read_text(encoding="utf-8") == "new file"
    assert (target / "legacy.txt").read_text(encoding="utf-8") == "keep"


def _assert_relaunched(target):
    launches = target / "launches.txt"
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if launches.exists() and launches.read_text(encoding="utf-8-sig").splitlines():
            assert launches.read_text(encoding="utf-8-sig").splitlines() == ["new release"]
            return
        time.sleep(0.05)
    pytest.fail("successful update did not launch the newly installed launcher")


def test_installer_waits_for_launcher_before_writing(update_installer, tmp_path):
    target = _target(tmp_path)
    launcher = _start_launcher()
    installer = None
    try:
        installer, log = _start_installer(update_installer, target, launcher.pid)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if log.exists() and "Waiting for launcher process to exit." in log.read_text(encoding="utf-8-sig"):
                break
            assert installer.poll() is None, "installer exited before waiting for the launcher"
            time.sleep(0.05)
        else:
            pytest.fail("installer did not reach the launcher-exit gate")
        _assert_unchanged(target)
        assert launcher.poll() is None
        launcher.communicate(timeout=5)
        assert installer.wait(timeout=30) == 0
        _assert_installed(target)
        _assert_relaunched(target)
    finally:
        if launcher.poll() is None:
            launcher.communicate(timeout=5)
        if installer is not None and installer.poll() is None:
            installer.wait(timeout=45)


def test_installer_handles_an_already_exited_launcher(update_installer, tmp_path):
    target = _target(tmp_path)
    with _start_launcher() as launcher:
        launcher.communicate(timeout=5)
        installer, _ = _start_installer(update_installer, target, launcher.pid)
        with installer:
            assert installer.wait(timeout=30) == 0
    _assert_installed(target)
    _assert_relaunched(target)


def test_installer_leaves_files_unchanged_on_exit_timeout(update_installer, tmp_path):
    target = _target(tmp_path)
    with _start_launcher() as launcher:
        try:
            installer, _ = _start_installer(update_installer, target, launcher.pid)
            with installer:
                assert installer.wait(timeout=45) != 0
            assert launcher.poll() is None, "installer killed a process instead of waiting for cleanup"
            _assert_unchanged(target)
        finally:
            launcher.communicate(timeout=5)


def test_installer_rejects_an_invalid_launcher_pid(update_installer, tmp_path):
    target = _target(tmp_path)
    installer, _ = _start_installer(update_installer, target, "invalid")
    with installer:
        assert installer.wait(timeout=30) != 0
    _assert_unchanged(target)
