# -*- coding: utf-8 -*-
"""可选落盘追踪日志。

默认**关闭**。设 ``T7_TRACE_LOG`` 为任意非空值（``1 / on / yes`` 均可）即开启：
把原本只活在 ``flow.result['logs']``（每条消息响应内、不落盘）的关键事件——
``move-blocked-*``（路卡点）、``cc-object-*`` / ``mo-push-*``（攻城器械收发）——
实时追加到 ``server/data/trace_log.txt``，方便离线复盘。

为什么需要
----------
用户要「看一下日志」定位樊城的路卡点 / 攻城器械是否发出。但：
* 客户端 ``tlog/*.log`` 是加密二进制，直接读是乱码；
* 服务端 ``flow.result['logs']`` 只跟每条消息走，进程不落盘，事后抓不到；
* 唯一的明文服务端日志（``_stdout.log``）只有一行场景契约。

所以加这个开关：进图后走到卡点 / 靠近器械，``trace_log.txt`` 里就有精确坐标。

env
---
``T7_TRACE_LOG``  : 任意非空（非 0/off/false/no）→ 开启
``T7_TRACE_LOG_PATH`` : 自定义输出路径（默认 ``server/data/trace_log.txt``）

ini
---
2026-09-23 夜新增 ``[trace]`` 段（``level.ini``）——**环境变量 > ini > 默认**：

    [trace]
    enabled=on          ; on/1 → 开；off/0 → 关
    path=               ; 留空 = 默认 server/data/trace_log.txt

为什么也走 ini：``T7_TRACE_LOG`` 是环境变量，而服务端由 ``T7.Launcher.exe``
拉起，终端里 ``set`` 的变量**进不去进程** —— 2026-09-23 就因此吃过一次亏：
``data/trace_log.txt`` 一直是 **0 字节**，现场只能误判成「开关没写对」，
实际是「开关根本没进进程」。现在启动时会打一行 ``[trace] ... source=``，
读一次即定案。

纪律
----
* 落盘失败一律吞掉，**绝不**影响正常移动 / 进图结算（和 collide / ccobject 同一套）。
* 每行格式：``时间\\t标签\\tscene=xxx\\t消息``，标签 block / cc / mo，方便 grep。

时间（2026-10-01 统一）
----
* **本文件行首时间 = 北京时间**（ISO8601 + 毫秒 + ``+08:00``），如
  ``2026-10-01T00:17:19.123+08:00``。
* ⚠️ **wire 日志**（``data/<会话>/wire/frames-1.jsonl`` 的 ``timestamp``）
  由 ``T7.Server.exe`` 原生层写，**是 UTC**（结尾 ``Z``），比北京**晚 8 小时**。
  两份日志对照时：**北京 = wire + 8 小时**。
* 换算工具：``python wire_time_to_bj.py <会话目录>``（本仓库 ``server/`` 根下）。
"""
import os
import time

from . import contracts as wire

_SWITCH = "T7_TRACE_LOG"
_PATH_ENV = "T7_TRACE_LOG_PATH"
_TRACE_INI_SECTION = "trace"
_TRACE_INI_ENV = "T7_TRACE_INI"
_cache = None


def _traceIniRaw():
    """把 ``level.ini`` / ``server.ini`` 的 ``[trace]`` 段读成 dict；读不到就是空 dict。

    实现走 ``contracts.iniSection``（2026-09-23 夜收成共享实现）。
    ⚠️ 值不要写行内注释（ConfigParser 默认不认），注释单独占行。
    """
    return wire.iniSection(_TRACE_INI_SECTION, _TRACE_INI_ENV)


_TRACE_INI = _traceIniRaw()


def _path():
    """定位 ``server/data/trace_log.txt``（脚本会被快照到 revisions 下，必须逐级向上找）。"""
    global _cache
    if _cache is not None:
        return _cache
    override = os.environ.get(_PATH_ENV)
    if override:
        _cache = override
        return _cache
    fromIni = str(_TRACE_INI.get("path", "")).strip()
    if fromIni:
        _cache = fromIni
        return _cache
    try:
        for node in wire.walkUp():
            candidate = os.path.join(node, "data")
            if os.path.isdir(candidate):
                _cache = os.path.join(candidate, "trace_log.txt")
                return _cache
    except (AttributeError, TypeError, ValueError):
        pass
    _cache = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "data", "trace_log.txt")
    return _cache


def enabled():
    """**环境变量 > ``[trace] enabled`` > 关闭**。"""
    value = os.environ.get(_SWITCH)
    if value is not None and value.strip():
        return value.strip().lower() not in ("0", "off", "false", "no")
    fromIni = str(_TRACE_INI.get("enabled", _TRACE_INI.get("log", ""))).strip().lower()
    if fromIni in ("1", "on", "true", "yes"):
        return True
    if fromIni in ("0", "off", "false", "no"):
        return False
    return False


# ---- 时间戳：一律北京时间（UTC+8），带毫秒 + 显式时区标记 ----------------
#
# ⚠️ 2026-10-01：本机 ``time.localtime()`` 就是 UTC+8（北京时间），所以
#    ``time.strftime`` 本来已经是北京时间。但有两个坑：
#    ① 原格式只到「秒」，而 wire 日志（``frames-1.jsonl``）到毫秒 ⇒ 对不上；
#    ② wire 日志的时间戳是 **UTC**（``...T15:59:04.299Z``，由 T7.Server.exe
#       原生层生成，Python 改不了源头），复盘时和本文件差 8 小时，极易误判。
#    所以本文件统一输出 ISO8601 + ``+08:00`` 后缀，并在前缀标 ``[BJ]``，
#    一眼就能和 wire 的 ``Z``（UTC）区分开；换算关系：北京 = wire + 8h。
_TZ_BJ = "+08:00"


def stamp(now=None):
    """北京时间 ISO8601（毫秒精度 + 时区后缀），如 ``2026-10-01T00:17:19.123+08:00``。

    ``now`` 传 ``time.time()`` 的浮点秒；不传就取当前。**不抛异常**。
    """
    try:
        t = time.time() if now is None else float(now)
        lt = time.localtime(t)
        ms = int(round((t - int(t)) * 1000))
        if ms >= 1000:          # 四舍五入进位的边界
            ms = 999
        return "%s.%03d%s" % (time.strftime("%Y-%m-%dT%H:%M:%S", lt), ms, _TZ_BJ)
    except (OSError, OverflowError, ValueError):
        return "0000-00-00T00:00:00.000" + _TZ_BJ


def emit(tag, scene, message):
    """写一行到追踪日志（开关关闭 / 落盘失败都静默返回）。

    行格式（第 1 列 = 北京时间，带 ``+08:00``）::

        2026-10-01T00:17:19.123+08:00	TAG	scene=xxx	消息

    ⚠️ 与 wire 日志（``frames-1.jsonl`` 的 UTC ``Z``）差 8 小时，别混。要对照就把
    本文件时间**减 8 小时** = wire 时间；或直接用 ``hkx_decode/../wire_time_to_bj.py``。
    """
    if not enabled():
        return
    try:
        line = "%s\t%s\tscene=%s\t%s\n" % (
            stamp(), tag,
            scene if isinstance(scene, str) else "", message)
        with open(_path(), "a", encoding="utf-8") as handle:
            handle.write(line)
    except OSError:
        pass


# 启动自证行（与 ``[contracts]`` / ``[move]`` / ``[cc]`` 那几行同一风格）。
# ``source=`` 指出开关来自哪条通道；``file=`` 是实际落盘路径 ——
# 「日志是空的」到底是没开、还是开到了别的路径，这一行同时给出答案。
# ``tz=`` 2026-10-01 新增：写明本文件时间戳是北京时间，以及与 wire 日志的换算差。
print("[trace] enabled=" + ("on" if enabled() else "off")
      + " source=" + ("env" if (os.environ.get(_SWITCH) or "").strip()
                      else (_TRACE_INI_SECTION if _TRACE_INI else "default"))
      + " tz=" + _TZ_BJ + "(本地; wire日志=-8h)"
      + " file=" + _path(), flush=True)

# ⭐ 2026-09-24：把「武器启用位开关到底生效没」也落盘一份。_stdout.log 抓到的是
#    老进程（只有一行、时间戳不动），所以 A/B 的生效值改从 trace_log 这条路看。
emit("battle", "", "[trace-boot] all_using="
     + ("on" if getattr(wire, "allWeaponsUsing", lambda: False)() else "off")
     + " throw_ammo=" + str(getattr(wire, "_throwAmmo", lambda *_: "?")(1080311))
     + " hero1101=" + repr(getattr(wire, "battleLoadout", lambda *_: None)(1101)))
