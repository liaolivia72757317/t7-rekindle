# -*- coding: utf-8 -*-
"""将星录名册（``hero_roster.json``）的**可复现生成流程**。

背景
----
``src/Business/data/`` 在 ``.gitignore`` 里（归类为「Runtime state and diagnostics」），
所以 ``data/hero/hero_roster.json`` **不会进版本库**。干净检出后文件不存在，
``contracts._loadHeroRoster()`` 必须能退到一份**有效的内置默认名册**，
否则 ``battleLoadout()`` 返回空列表 ⇒ 进图空手、选将只剩一张卡。

因此本仓库把「默认名册」作为**代码里的单一数据源**（``scripts/herodefault.py``），
本工具负责它与 JSON 之间的双向搬运：

    # 1) 从本地 JSON 重新生成内置默认（数据源变更后跑一次，产物要提交）
    python tools/hero_roster.py --emit-module

    # 2) 把内置默认写成运行期 JSON（部署 / 本地调试用）
    python tools/hero_roster.py --write

    # 3) 只校验：内置默认与本地 JSON 是否一致（CI / 回归用）
    python tools/hero_roster.py --check

JSON 结构（各字段语义见 ``herodefault.py`` 顶部注释）
----------------------------------------------------
``note`` / ``containerCardLimit`` / ``currency`` / ``cards`` / ``souls`` /
``unlocks`` / ``battleFormation`` / ``weapons``

⚠️ 只读 / 只写上述文件，不碰其它任何东西。
"""
import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "src", "Business", "scripts")
MODULE = os.path.join(SCRIPTS, "herodefault.py")
DEFAULT_JSON = os.path.join(ROOT, "src", "Business", "data", "hero", "hero_roster.json")

sys.path.insert(0, os.path.dirname(SCRIPTS))   # 让 `scripts` 成为可导入包

# 字段顺序固定 —— 生成结果必须逐字节稳定，否则每次 --emit-module 都是假 diff。
FIELDS = ("note", "containerCardLimit", "currency", "cards", "souls",
          "unlocks", "battleFormation", "weapons")

MODULE_HEADER = '''# -*- coding: utf-8 -*-
"""将星录名册的**内置默认**（单一数据源）。

⚠️ 本文件由 ``tools/hero_roster.py --emit-module`` 生成，**不要手改**；
   要改数据请改 ``src/Business/data/hero/hero_roster.json`` 后重新生成。

为什么要放在代码里：``src/Business/data/`` 在 ``.gitignore`` 里（运行期数据），
干净检出时 ``hero_roster.json`` 不存在。若没有这份内置默认，
``contracts._loadHeroRoster()`` 只能退回「一张卡 + 空装备」，
导致 ``battleLoadout()`` 返回空列表（进图空手）、骑兵验证脚本直接失败。

各字段语义（与 JSON 完全同构）
------------------------------
``NOTE``            人读的说明，仅作记录，代码不使用。
``CONTAINER_LIMIT`` 将星录容器卡位上限。
``CURRENCY``        货币初始值 ``{名称: 数量}``。
``CARDS``           ``(pos, guid, herocard_tid)``。**pos 1 是战斗侧绑定认的那张，别动**。
``SOULS``           ``(pos, guid, 将魂tid, 数量)``。
``UNLOCKS``         ``{解锁槽: (pos, guid, herocard_tid)}``。
``BATTLE_FORMATION`` 出战阵容 —— 引用 ``CARDS`` 的 pos。
``WEAPONS``         ``(pos, 武器槽, set下标, 组号, 组内序号, 武器tid, 锁定态)``。
                    组号出自《武将装备链完整数据》§七：group 即装备组编号；
                    坐骑组的 tid 会走 ``mount_tid`` 而不是武器列表。
"""

'''

MODULE_FOOTER = '''

def asRoster():
    """按 ``_loadHeroRoster()`` 的返回结构给出名册字典。

    键名与 ``_loadHeroRoster()`` 逐字对齐，直接 ``return`` 即可当作兜底值。
    """
    return {
        "cards": CARDS,
        "souls": SOULS,
        "currency": dict(CURRENCY),
        "limit": CONTAINER_LIMIT,
        "unlocks": dict(UNLOCKS),
        "battleFormation": BATTLE_FORMATION,
        "weapons": WEAPONS,
        "source": "builtin",
    }
'''


def loadJson(path):
    with open(path, encoding="utf-8") as fp:
        return json.load(fp)


def toModule(data):
    """JSON dict -> herodefault.py 源码。"""
    out = [MODULE_HEADER]
    out.append("NOTE = " + pyStr(data.get("note", "")) + "\n\n")
    out.append("CONTAINER_LIMIT = %d\n\n" % int(data.get("containerCardLimit", 50)))

    out.append("CURRENCY = {\n")
    for key, value in data.get("currency", {}).items():
        out.append("    %s: %d,\n" % (pyStr(key), int(value)))
    out.append("}\n\n")

    out.append("CARDS = (\n")
    for row in data.get("cards", []):
        out.append("    (%s),\n" % ", ".join(str(int(v)) for v in row))
    out.append(")\n\n")

    out.append("SOULS = (\n")
    for row in data.get("souls", []):
        out.append("    (%s),\n" % ", ".join(str(int(v)) for v in row))
    out.append(")\n\n")

    out.append("UNLOCKS = {\n")
    for key, row in data.get("unlocks", {}).items():
        out.append("    %d: (%s),\n" % (int(key), ", ".join(str(int(v)) for v in row)))
    out.append("}\n\n")

    out.append("BATTLE_FORMATION = (%s)\n\n"
               % ", ".join(str(int(v)) for v in data.get("battleFormation", [])))

    out.append("WEAPONS = (\n")
    for row in data.get("weapons", []):
        out.append("    (%s),\n" % ", ".join(str(int(v)) for v in row))
    out.append(")\n")
    out.append(MODULE_FOOTER)
    return "".join(out)


def pyStr(value):
    """输出稳定的 Python 字符串字面量。

    直接写 UTF-8 原文（仓库本来就有大量中文注释），只转义反斜杠 / 引号 / 换行，
    保证 ``--emit-module`` 反复跑结果逐字节一致。
    """
    text = (str(value).replace("\\", "\\\\").replace('"', '\\"')
            .replace("\r\n", "\\n").replace("\n", "\\n").replace("\r", "\\n"))
    return '"' + text + '"'


def moduleToJson():
    """把内置默认导回 JSON dict（字段顺序按 FIELDS）。"""
    from scripts import herodefault as hd
    return {
        "note": hd.NOTE,
        "containerCardLimit": hd.CONTAINER_LIMIT,
        "currency": dict(hd.CURRENCY),
        "cards": [list(r) for r in hd.CARDS],
        "souls": [list(r) for r in hd.SOULS],
        "unlocks": {str(k): list(v) for k, v in hd.UNLOCKS.items()},
        "battleFormation": list(hd.BATTLE_FORMATION),
        "weapons": [list(r) for r in hd.WEAPONS],
    }


def emitModule():
    if not os.path.isfile(DEFAULT_JSON):
        print("✗ 找不到 %s —— 无法生成内置默认" % DEFAULT_JSON)
        return 2
    source = toModule(loadJson(DEFAULT_JSON))
    with open(MODULE, "w", encoding="utf-8", newline="\n") as fp:
        fp.write(source)
    print("✓ 已生成 %s（%d 字节）" % (MODULE, len(source.encode("utf-8"))))
    return 0


def writeJson():
    data = moduleToJson()
    os.makedirs(os.path.dirname(DEFAULT_JSON), exist_ok=True)
    with open(DEFAULT_JSON, "w", encoding="utf-8", newline="\n") as fp:
        json.dump(data, fp, ensure_ascii=False, indent=1)
        fp.write("\n")
    print("✓ 已写出 %s" % DEFAULT_JSON)
    print("   cards=%d souls=%d weapons=%d unlocks=%d formation=%s"
          % (len(data["cards"]), len(data["souls"]), len(data["weapons"]),
             len(data["unlocks"]), data["battleFormation"]))
    return 0


def check():
    """内置默认 vs 本地 JSON：只在本地 JSON 存在时比较（干净检出时跳过）。"""
    if not os.path.isfile(DEFAULT_JSON):
        print("· 本地没有 %s（干净检出属正常）—— 只校验内置默认自身可用性" % DEFAULT_JSON)
        data = moduleToJson()
    else:
        data = loadJson(DEFAULT_JSON)
        builtin = moduleToJson()
        if data != builtin:
            print("✗ 内置默认与本地 JSON **不一致** —— 跑 --emit-module 同步")
            for key in FIELDS:
                if data.get(key) != builtin.get(key):
                    print("   · 字段不同: %s" % key)
            return 1
        print("✓ 内置默认与本地 JSON 一致")

    problems = []
    if not data["cards"]:
        problems.append("cards 为空")
    if not data["weapons"]:
        problems.append("weapons 为空（⇒ battleLoadout 会返回空列表）")
    if not data["battleFormation"]:
        problems.append("battleFormation 为空")
    positions = {row[0] for row in data["cards"]}
    for position in data["battleFormation"]:
        if position not in positions:
            problems.append("battleFormation 的 pos %s 不在 cards 里" % position)
    tidByPos = {row[0]: row[2] for row in data["cards"]}
    for position in data["battleFormation"]:
        tid = tidByPos.get(position)
        if tid is not None and not [w for w in data["weapons"] if w[0] == position]:
            problems.append("出战 pos %s(tid=%s) 没有任何武器行" % (position, tid))
    if problems:
        print("✗ 名册不自洽：")
        for item in problems:
            print("   · %s" % item)
        return 1
    print("✓ 名册自洽：cards=%d weapons=%d formation=%s"
          % (len(data["cards"]), len(data["weapons"]), data["battleFormation"]))
    return 0


def main():
    parser = argparse.ArgumentParser(description="将星录名册生成 / 校验")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--emit-module", action="store_true",
                       help="从 data/hero/hero_roster.json 生成 scripts/herodefault.py")
    group.add_argument("--write", action="store_true",
                       help="把内置默认写成 data/hero/hero_roster.json")
    group.add_argument("--check", action="store_true",
                       help="校验内置默认与本地 JSON 是否一致、名册是否自洽")
    args = parser.parse_args()
    if args.emit_module:
        return emitModule()
    if args.write:
        return writeJson()
    return check()


if __name__ == "__main__":
    sys.exit(main())
