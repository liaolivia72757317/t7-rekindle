"""Python oracles remain importable but never become product assets."""
from pathlib import Path
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]

def test_desktop_only_copies_static_news():
    project = ET.parse(ROOT / "src/Desktop/T7R.Desktop.csproj").getroot()
    imports = {node.get("Project") for node in project.findall("Import")}
    assert "RuntimeAssets.targets" in imports
    assert "BusinessScripts.targets" not in imports
    targets = ET.parse(ROOT / "src/Desktop/RuntimeAssets.targets").getroot()
    assets = targets.findall(".//None")
    assert len(assets) == 1 and assets[0].get("Include") == r"Resources\News\**\*"
    assert (ROOT / "tests/reference/Business/runtime/host_runtime.py").is_file()


def test_reference_protocol_and_export_tools_still_import():
    from Business.scripts.codec import title_flow
    assert title_flow.setResult(True) == bytes.fromhex("000600000000")
