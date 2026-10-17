from pathlib import Path
import shutil
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("missingStreams", [(), ("stdout",), ("stderr",), ("stdin", "stdout", "stderr")])
def test_packaged_dependencies_and_business_reload_without_site_packages(tmp_path, missingStreams):
    business = tmp_path / "Business"
    shutil.copytree(ROOT / "tests/reference/Business", business, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    code = r"""
from pathlib import Path
import sys
root = Path(sys.argv[1])
originalStreams = {name: getattr(sys, name) for name in ('stdin', 'stdout', 'stderr')}
missingStreams = sys.argv[2:]
for name in missingStreams:
    setattr(sys, name, None)
sys.path.insert(0, str(root / 'Business/runtime'))
import host_runtime
import py_trees, pydot, pyparsing
for name, original in originalStreams.items():
    stream = getattr(sys, name)
    if name not in missingStreams:
        assert stream is original
    elif name != 'stdin':
        assert stream.encoding == 'utf-8'
        assert stream.writable()
        assert stream.write('console probe') == len('console probe')
        stream.flush()
    else:
        assert stream is None
for module in (py_trees, pydot, pyparsing):
    assert Path(module.__file__).is_relative_to(root / 'Business/vendor')
assert py_trees.version.__version__ == '2.6.0'
assert pydot.__version__ == '4.0.1'
assert pyparsing.__version__ == '3.3.3'
runtime = host_runtime.Runtime(root / 'Business/scripts', root / 'cache')
try:
    state = runtime.create(host_runtime.encode({'runtimeMovement': True}))
    runtime.prepare()
    assert runtime.switch(state) == state
finally:
    runtime.close()
originalStreams['stdout'].write('isolated-vendor-ok\n')
"""
    result = subprocess.run([sys.executable, "-I", "-S", "-W", "error::ResourceWarning", "-c", code,
                             str(tmp_path), *missingStreams],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "isolated-vendor-ok"
    assert result.stderr == ""


@pytest.mark.skipif(sys.platform != "win32", reason="Windows GUI process standard handles")
def test_windows_no_console_process_loads_and_reloads_business(tmp_path):
    business = tmp_path / "Business"
    shutil.copytree(ROOT / "tests/reference/Business", business, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    report = tmp_path / "result.txt"
    code = r"""
from pathlib import Path
import sys
import traceback
root = Path(sys.argv[1])
try:
    assert sys.stdin is None and sys.stdout is None and sys.stderr is None
    sys.path.insert(0, str(root / 'Business/runtime'))
    import host_runtime
    runtime = host_runtime.Runtime(root / 'Business/scripts', root / 'cache')
    try:
        state = runtime.create(host_runtime.encode({'runtimeMovement': True}))
        runtime.prepare()
        assert runtime.switch(state) == state
    finally:
        runtime.close()
    (root / 'result.txt').write_text('ok', encoding='utf-8')
except BaseException:
    (root / 'result.txt').write_text(traceback.format_exc(), encoding='utf-8')
    raise SystemExit(1)
"""
    startup = subprocess.STARTUPINFO()
    startup.dwFlags = subprocess.STARTF_USESTDHANDLES
    startup.hStdInput = startup.hStdOutput = startup.hStdError = 0
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    result = subprocess.run([str(pythonw), "-I", "-S", "-c", code, str(tmp_path)],
                            startupinfo=startup, creationflags=subprocess.CREATE_NO_WINDOW, timeout=30)
    detail = report.read_text(encoding="utf-8") if report.exists() else "no startup report"
    assert result.returncode == 0, detail
    assert detail == "ok"


def test_reference_dependency_pins_and_licenses_are_preserved_with_vendor():
    from email.parser import Parser

    vendor = ROOT / "tests/reference/Business/vendor"
    readme = (vendor / "README.md").read_text(encoding="utf-8")
    for pin in (vendor / "requirements.txt").read_text(encoding="utf-8").splitlines():
        name, version = pin.split("==")
        assert f"[{name}]" in readme and f"| {version} |" in readme
        metadata_root = vendor / f"{name}-{version}.dist-info"
        metadata = Parser().parsestr((metadata_root / "METADATA").read_text(encoding="utf-8"))
        assert metadata["Name"] == name and metadata["Version"] == version
        licenses = metadata.get_all("License-File")
        assert licenses
        for license_file in licenses:
            assert (metadata_root / "licenses" / license_file).read_text(encoding="utf-8").strip()
