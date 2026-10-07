"""干净检出时名册不许退化：``herodefault.py`` 内置默认必须兜住。

需求
----
``src/Business/data/`` 是运行期数据、不进版本库，所以**干净检出没有**
``data/hero/hero_roster.json``。若退回「只有 pos 1 一张卡 + 空装备」：

    empty = {..., "weapons": (), "source": "fallback"}

``_heroWeapons()`` 拿到空 weapons ⇒ ``HERO_CARD_WEAPONS = {}`` ⇒
``HERO_BATTLE_LOADOUTS = {}`` ⇒ ``battleLoadout()`` 返回**空列表**：进图空手，
坐骑链路（``src/Business/scripts/verify_mount_turn.py``）直接失败。

正确行为是退回 ``src/Business/scripts/herodefault.py`` 里的内置默认
（``source == "builtin"``），并且**与有文件时逐值相同**。

为什么用子进程 + 复制目录
------------------------
``contracts`` 的模块级常量（``HERO_CARDS`` / ``HERO_BATTLE_LOADOUTS`` …）在
import 时就定型了，在当前进程里改 ``HERO_ROSTER_PATH`` 或 reload 都容易污染其它
测试。复制一份不含 ``data/`` 的 ``Business`` 树、在干净子进程里 import，才真正
等价于「干净检出」。
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BUSINESS = ROOT / "src" / "Business"
TOOL = ROOT / "tools" / "hero_roster.py"

# 子进程里打印一行 JSON，主进程解析；避免依赖 pytest 的输出捕获格式。
PROBE = r"""
import json, sys
sys.path.insert(0, sys.argv[1])
from Business.scripts import contracts as c
held, mount = c.battleLoadout(1101)
print("PROBE " + json.dumps({
    "source": c._HERO_ROSTER_SOURCE,
    "cards": len(c.HERO_CARDS),
    "souls": len(c.HERO_SOULS),
    "limit": c.HERO_CONTAINER_LIMIT,
    "weaponPositions": sorted(c.HERO_CARD_WEAPONS),
    "formation": [card[0] for card in c.HERO_BATTLE_FORMATION],
    "heroIds": list(c.HERO_IDS),
    "loadout": [entry[1] for entry in held],
    "mount": mount,
    "loadoutCount": len(c.HERO_BATTLE_LOADOUTS),
}))
"""


def _probe(tree):
    proc = subprocess.run([sys.executable, "-c", PROBE, str(tree)],
                          capture_output=True, text=True, cwd=str(tree))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    lines = [line for line in proc.stdout.splitlines() if line.startswith("PROBE ")]
    assert lines, proc.stdout + proc.stderr
    return json.loads(lines[-1][len("PROBE "):])


def _cleanTree(tmp_path, name):
    """复制 ``src/Business``，但**不含 data/** —— 等价于干净检出。"""
    tree = tmp_path / name
    shutil.copytree(BUSINESS, tree / "Business",
                    ignore=shutil.ignore_patterns("data", "__pycache__", "cache", "*.pyc"))
    assert not (tree / "Business" / "data").exists()
    return tree


def test_clean_checkout_falls_back_to_builtin_roster(tmp_path):
    got = _probe(_cleanTree(tmp_path, "clean"))
    assert got["source"] == "builtin", got
    # 内置默认必须是完整名册，不是「一张卡」
    assert got["cards"] == 87 and got["souls"] == 38 and got["limit"] == 50
    assert got["weaponPositions"] == [1, 2, 3, 4, 5, 34, 45, 52, 67, 81]
    assert got["formation"] == [1, 3, 4, 5]
    assert got["heroIds"] == [1101, 3012, 3031, 2121]
    # ⭐ 这条就是审查意见 P1-1 的判据：默认装备不许退化
    assert got["loadout"] == [1030411, 1080311, 1080511], got
    assert got["mount"] == 0
    assert got["loadoutCount"] == 10


def test_builtin_matches_file_roster(tmp_path):
    """有文件（``source=file``）与无文件（``source=builtin``）必须逐值相同。"""
    with_file = _probe(ROOT / "src")
    without = _probe(_cleanTree(tmp_path, "clean"))
    assert with_file["source"] == "file", with_file
    assert without["source"] == "builtin", without
    for key in ("cards", "souls", "limit", "weaponPositions", "formation",
                "heroIds", "loadout", "mount", "loadoutCount"):
        assert with_file[key] == without[key], (key, with_file[key], without[key])


def test_roster_tool_check_is_green():
    """``tools/hero_roster.py --check`` 必须通过（内置默认 ↔ JSON 同源、名册自洽）。"""
    proc = subprocess.run([sys.executable, str(TOOL), "--check"],
                          capture_output=True, text=True, cwd=str(ROOT))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "hero_roster" in proc.stdout or "✓" in proc.stdout, proc.stdout
