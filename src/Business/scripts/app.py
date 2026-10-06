"""Versioned fixed business adapter; native code owns sockets and timer lifetime."""
from copy import deepcopy
import struct

from . import contracts as wire, scene, controls, battle, dungeon, mo, npc_ai, tracelog
from .codec import login_flow, protocol, room_flow
from .codec.method3 import Method3UplinkMessage

API_VERSION = 1
STATE_VERSION = 2


def _readHallTimeCfg():
    """读 ``_hall_time.txt``（可把服务器时间钉进会战窗口）。

    脚本会被 `runtime/host_runtime.Revision` **复制**到
    ``data/<会话>/revisions/t7rev_xxx/`` 再加载，所以不能只认 ``__file__`` 同目录：
    从那儿上溯 4 层正好回到 ``server/``。也顺手兜一个绝对路径。
    """
    import os
    here = os.path.dirname(os.path.abspath(__file__))
    cands = []
    p = here
    for _ in range(4):
        p = os.path.dirname(p)
        cands.append(os.path.join(p, "_hall_time.txt"))
    cands.append(r"D:\流星\T7\server\_hall_time.txt")
    for c in cands:
        try:
            with open(c, "r", encoding="utf-8") as fp:
                line = fp.readline().strip()
            if line:
                return line
        except OSError:
            continue
    return ""


def epochMs():
    """交给客户端的「大厅时间」（epoch 毫秒）—— ``CS_PROTO_HALL_TIME_DATA``。

    ⚠️ 绝不能用 ``Flow.now`` 顶替：那是原生层传进来的**单调时钟**
    （本地日志实测 399639882 ms ≈ 4.6 天），客户端换算出来是 1970-01-05，
    照样判「此模式还未到开放时间」。

    ``_hall_time.txt``（放在 ``D:\\流星\\T7\\server\\``，改完重启生效）：
        空 / ``now``   → 真实时间
        ``19:35``      → 今天的 19:35（会战窗口 19:30-20:59 内）
        ``<整数>``     → 直接当 epoch 秒（10 位）或毫秒（13 位）
    """
    import time
    raw = _readHallTimeCfg()
    if not raw or raw.lower() in ("now", "real"):
        return int(time.time() * 1000)
    if ":" in raw:
        try:
            hh, mm = (int(x) for x in raw.split(":", 1))
        except ValueError:
            return int(time.time() * 1000)
        lt = time.localtime()
        # mktime 按**本地时区**解，和客户端判窗口的口径一致。
        return int(time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday,
                                hh, mm, 0, 0, 0, -1))) * 1000
    try:
        value = int(raw)
    except ValueError:
        return int(time.time() * 1000)
    return value * 1000 if value < 10000000000 else value


def createState(context):
    # 宿主（T7.NativeBridge）在内存客户端 overlay 装好后会往 context 注入
    # runtimeMovement（见 Runtime/core/Common.h 与 Runtime/server/PythonHost.cpp）。
    # 自建的 T7.Server.exe 不注入 ⇒ 取默认 False ⇒ 与改动前逐位相同。
    runtimeMovement = context.get("runtimeMovement", wire.CLIENT_RUNTIME_MOVEMENT)
    if type(runtimeMovement) is not bool:
        raise ValueError("runtimeMovement must be a bool")
    return {"phase": "waiting", "sessions": {}, "roomId": 1, "actorId": 1,
            "runtimeMovement": runtimeMovement}


def validateState(state):
    if (type(state) is not dict or type(state.get("sessions")) is not dict
            or type(state.get("phase")) is not str or state.get("roomId") != 1
            or state.get("actorId") != 1):
        return False
    # 旧状态没有这个键 ⇒ .get 取默认 False（仍是 bool）⇒ 不会把老存档判死。
    if type(state.get("runtimeMovement", wire.CLIENT_RUNTIME_MOVEMENT)) is not bool:
        return False
    return all(type(key) is str and type(session) is dict
               and session.get("role") in ("login", "logic", "instance")
               and type(session.get("hydration")) is list
               and type(session.get("pending", {})) is dict
               for key, session in state["sessions"].items())


def migrateState(fromVersion, state):
    if fromVersion not in (1, STATE_VERSION) or not validateState(state):
        raise ValueError("no migration for this state version")
    return deepcopy(state)


def selfTest():
    # 战斗侧装备是名册推出来的，VISION 长度跟着武器条数走，别写死把数。
    # ⭐ 2026-10-01（第十六轮）：这两个数都必须取**当前生效武将**（`_ACTIVE_HERO`
    #    会随关卡换），不能用加载时常量 `wire.HERO_ID` —— 否则切图换将后
    #    `actorVision()` 发的是新武将（武器条数不同），这里还按旧武将算 ⇒ 假报警。
    visionWeapons = len(wire.battleLoadout(wire._ACTIVE_HERO)[0])
    # ⭐ 2026-10-01（第十六轮）：带坐骑的武将（姜维 2121）`actorVision()` 会**再追加
    #    一个 mount VISION 对象**（`encode_mount_vision_object`），比步兵多一截。
    #    不把它算进来，骑兵关卡必然假报警（实测姜维 585 vs 公式 475）。
    visionMount = 0
    if wire.battleLoadout(wire._ACTIVE_HERO)[1]:
        visionMount = len(wire.vision_flow.encode_mount_vision_object(
            rid=wire.MOUNT_VISION_RID, res_id=wire.MOUNT_SCENE_RES_ID,
            position=wire.POSITION, instance_id=wire.MOUNT_VISION_INSTANCE_ID))
    # 出战容器每槽 = 88B 基线 + 每把武器一条 106B CS_BATTLE_HERO_WEAPON_SLOT_DEF。
    entrySize = wire.login_flow._HERO_WEAPON_ENTRY_SIZE
    # ⭐ 2026-10-01（第十六轮）：这里**必须**跟 `battleHeroes()` 取同一份阵容
    #    （现算的 `battleFormationNow()`），否则切图换将后两者的槽位集合不同、
    #    长度对不上 ⇒ selfTest 假报警。语义：校验的是「编码器与容器布局一致」，
    #    不是「阵容等于某个常量」。
    battleSlots = sum(88 + entrySize * len(wire.battleLoadout(hero)[0])
                      for (_position, _guid, hero) in wire.battleFormationNow())
    return (protocol.encode_version_check_response(0) == bytes.fromhex("006500000000")
            and len(wire.roundState(0, 2, 30000)) == 42
            and len(wire.actorVision(1)) == 368 + len(wire.USER_NAME)
                + 29 * visionWeapons + visionMount
            and len(wire.battleHeroes(13)) == 28 + battleSlots)


class Flow:
    def __init__(self, event, state, context):
        self.state, self.context = state, context
        self.connection = event["connection"]
        self.now = context["nowMs"]
        self.session = state["sessions"].get(str(self.connection))
        self.result = {"state": state, "send": [], "timers": [], "logs": []}

    def phase(self, name):
        self.session["phase"] = name
        activeInstance = any(session["role"] == "instance" and not session.get("leaving")
                             for session in self.state["sessions"].values())
        if self.session["role"] == "instance" or not activeInstance:
            self.state["phase"] = name

    def send(self, command, body, reason):
        self.result["send"].append({"connection": self.connection, "command": command,
                                    "body": body, "reason": reason})

    def later(self, name, delay):
        self.session.setdefault("pending", {})[name] = self.now + delay
        self.result["timers"].append({"id": f"{self.connection}:{name}", "delayMs": delay,
            "event": {"type": "timer", "connection": self.connection, "name": name}})

    def cancelTimers(self):
        for name in self.session.get("pending", {}):
            self.result["timers"].append({"id": f"{self.connection}:{name}", "delayMs": -1,
                "event": {"type": "timer", "connection": self.connection, "name": name}})
        self.session["pending"] = {}

    def cancel(self, name):
        if name in self.session.get("pending", {}):
            del self.session["pending"][name]
            self.result["timers"].append({"id": f"{self.connection}:{name}", "delayMs": -1,
                "event": {"type": "timer", "connection": self.connection, "name": name}})

    def syncBody(self):
        return protocol.encode_sync_login_response(protocol.SyncLoginResponse(10000, 1, 0, 0))


def handleTimer(flow, name):
    pending = flow.session.get("pending")
    if pending is None:
        # Old revision 1 stored native login timers but no script timer ledger.
        if name not in ("version", "login", "sync"):
            return
        flow.session["pending"] = {}
    elif name not in pending or flow.now < pending[name]:
        return
    else:
        flow.timerDue = pending[name]
        del pending[name]
    if name == "version":
        flow.send(1, protocol.encode_version_check_response(0), "version-response")
    elif name == "login":
        identity = protocol.MinimalLoginIdentity(
            wire.USER_ID, wire.USER_NAME, user_image_id=7, level=99,
            copper_coin=wire.HERO_CURRENCY.get("copper", 0),
            silver_coin=wire.HERO_CURRENCY.get("silver", 0),
            coupon=wire.HERO_CURRENCY.get("coupon", 0),
            point_ticket=wire.HERO_CURRENCY.get("pointTicket", 0))
        flow.send(1, protocol.encode_minimal_login_success(identity), "fixed-local-login")
        flow.later("sync", 20000)
    elif name == "sync":
        flow.send(0x2C, flow.syncBody(), "sync-login")
        flow.phase("hydrating")
    elif name == "match-result":
        flow.send(0x20, struct.pack(">Hi", 3, 0), "match-result-notify")
    elif name == "match-enter":
        flow.send(0x1E, room_flow.encode_room_enter_instance_notify(
            room_id=1, user_camp=1, notify_type=0), "match-enter-instance-notify")
        flow.phase("instance-notified")
    elif name == "hero-card-push":
        # 容器首包只装得下前 50 张（~268KB）。86 张一次给 = 460KB = 大厅黑屏
        # （2026-09-08 实测），所以超出的按 100ms 一张推 sub3 物品更新。
        # 时序是硬的：客户端登录后约 5.2s 打一次物品快照，快照之后到达的推送
        # 只进运行时库存、不进将星录界面 —— 38 张必须压在 4.5s 内推完。
        queue = flow.session.get("heroPushQueue")
        if queue:
            position, guid, hero_resource_id = queue.pop(0)
            flow.send(0x28, login_flow.encode_item_update_push(
                position=position, guid=guid,
                hero_resource_id=hero_resource_id,
                weapons=wire.HERO_CARD_WEAPONS.get(position, ())),
                "hero-card-update-push")
            flow.later("hero-card-push", 100)
        else:
            flow.send(0x28, login_flow.encode_item_container_notify(
                total_items=len(wire.HERO_CARDS)), "hero-card-container-notify")
            flow.session.pop("heroPushQueue", None)
    elif (not controls.timer(flow, name) and not scene.timer(flow, name)
          and not battle.timer(flow, name) and not mo.timer(flow, name)
          and not npc_ai.timer(flow, name)):
        raise ValueError(f"unknown business timer: {name}")


def handleRoom(flow, body, selector):
    syncUrl = f"{flow.context['advertisedAddress']}:{flow.context['instancePort']}".encode("ascii")
    if selector == 10:
        room_flow.decode_room_reconnect_request(body)
        flow.send(0x1E, room_flow.encode_room_reconnect_response(result=1), "room-reconnect-result-nonzero")
    elif selector in (1, 3):
        roomName, reconnect = b"RoomName1", 0
        if selector == 1:
            request = room_flow.decode_room_create_request(body)
            # ⭐ **按点到的卡片自动定图**（2026-09-19 用户要求）：
            #   点宛城之战卡片 → 只载入宛城；点攻城 → 只载入樊城；洛阳同理。
            #   以前只认 level.ini 里手填的 T7_LEVEL，不管点了哪张卡，
            #   于是「配了宛城却点攻城」会发宛城 → 客户端卡在载入地图。
            #   注意：**只有关卡真的变了才切**（洛阳默认路径不触发，逐位不变）。
            switchKind = wire.applyCard(request.resource_id)
            if switchKind:
                try:
                    flow.session["scene"] = wire.sceneFor()
                except (AttributeError, TypeError):
                    pass
                flow.result["logs"].append(
                    "level-auto-switch kind=" + switchKind
                    + " card=" + str(request.resource_id)
                    + " -> level=" + str(wire.LEVEL_ID)
                    + " scene=" + str(wire.sceneFor())
                    + " resource=" + str(wire.RESOURCE_ID)
                    + " spawn=" + str(wire.POSITION)
                    + " mode_a=" + str(wire.modeA()))
            if not wire.acceptsResourceId(request.resource_id):
                # ⚠️ 一定要把客户端**实际发来的** resource_id 打进日志：
                # 上行是加密的（frames-1.bin 里只能看到密文），这是唯一能
                # 看出「客户端到底点了哪张卡」的地方。
                raise ValueError("fixed native scene requires resource_id "
                                 + str(wire.RESOURCE_ID) + " (level="
                                 + str(wire.LEVEL_ID) + ", got="
                                 + str(request.resource_id) + ", pattern="
                                 + str(wire.PATTERN_BY_LEVEL.get(wire.LEVEL_ID))
                                 + ")")
            # ⚠️ **无条件**记这条：上行是加密的（frames-1.bin 里只有密文），
            # 这里是唯一能看到「客户端到底点了哪张卡」的地方。
            # 2026-09-19 实机翻车就靠它定位：日志显示 level=10036，
            # 才看出 T7_LEVEL 没进服务端进程。
            echoValue = wire.echoResourceId(request.resource_id)
            # ⚠️ 记住这个值：下面那条 **room-enter 响应**要用同一个 ID，
            # 而独立的 enter 请求（selector==3）**拿不到**客户端卡片 ID
            # （`RoomEnterRequest` 只有 room_id / is_reconnect，没有 resource_id
            #  —— 我 2026-09-19 在这里写过 `request.resource_id`，
            #    直接 AttributeError，客户端拿不到 enter 响应 → "点了没反应"）。
            try:
                flow.session["roomResourceId"] = echoValue
            except (AttributeError, TypeError):
                pass
            flow.result["logs"].append(
                "room-create-resource-id client=" + str(request.resource_id)
                + " echo=" + str(echoValue) + " level=" + str(wire.LEVEL_ID)
                + " echoMode=" + wire.echoMode()
                + " pattern=" + str(wire.PATTERN_BY_LEVEL.get(wire.LEVEL_ID))
                + " match=" + ("1" if request.resource_id == echoValue else "0"))
            roomName = request.room_name
            flow.send(0x1E, room_flow.encode_room_create_response(
                result=0, room_id=1, room_name=roomName, resource_id=echoValue), "room-create-result-zero")
        else:
            request = room_flow.decode_room_enter_request(body)
            if request.room_id != 1:
                raise ValueError("fixed native scene requires room_id 1")
            reconnect = request.is_reconnect
            # ⚠️ `RoomEnterRequest` **没有** resource_id 字段（只有 room_id /
            # is_reconnect）。独立的 enter 请求无从得知客户端卡片 ID：
            # 优先复用同一次 create 记住的值，没有才退回常量。
            remembered = None
            try:
                remembered = flow.session.get("roomResourceId")
            except (AttributeError, TypeError):
                remembered = None
            echoValue = remembered if isinstance(remembered, int) else wire.RESOURCE_ID
        flow.send(0x1E, room_flow.encode_room_enter_response(
            room_id=1, result=0, is_reconnect=reconnect, room_name=roomName,
            resource_id=echoValue, sync_url=syncUrl, prefer=0), "room-enter-result-zero")
        # ⚠️ 把「这条 room-enter 是在哪个角色的连接上发的、当时有没有 instance
        # 连接、sync_url 指向哪」全部记账：客户端下一步就是按 sync_url 去连
        # 房间服务器，连不上就弹「连接房间服务器失败」。
        try:
            flow.result["logs"].append(
                "room-enter-sent role=" + str(flow.session.get("role"))
                + " connection=" + str(getattr(flow, "connection", "?"))
                + " syncUrl=" + syncUrl.decode("ascii", "replace")
                + " sessions=" + str(len(flow.state.get("sessions", {})))
                + " roles=" + str(sorted({s.get("role") for s in
                                          flow.state.get("sessions", {}).values()}))
                + " hasInstance="
                + str(any(s.get("role") == "instance"
                          for s in flow.state.get("sessions", {}).values())))
        except (AttributeError, TypeError):
            pass
        flow.phase("room-data-sent")
    else:
        return False
    return True


def handleMessage(flow, event):
    body, command = event["body"], event["command"]
    if type(body) is not bytes or len(body) < 2:
        raise ValueError("business message requires a two-byte selector")
    selector = int.from_bytes(body[:2], "big")
    # ⭐ 2026-09-24：把**每一种**上行 (command,selector) 首次出现落盘到 trace_log，
    #    用来定位「按 2/3 换武器到底发了什么」。之前服务端根本不把上行写盘
    #    （flow.result['logs'] 只在内存里、进程不落盘），所以「查日志」无从查起。
    #    每种 key 只记一次、带一小段明文，避开 WASD/朝向那种每帧刷的噪声。
    #
    # ⚠️⚠️ 2026-09-24 10:52 事故（别再犯）：这段**最初把 tuple 当 dict 的 key**——
    #       ``upKey = (command, selector)``
    #     写进 session 后，原生层序列化会话 state 时只认 **str key 的 dict**，
    #     遇到非 str key 直接抛 ``unsupported state type: dict`` 并
    #     **丢弃整条消息** ⇒ 后续进图报文全不发 ⇒ **客户端黑屏进不去游戏**。
    #     症状（会话抓包，见 data/<会话>/wire/frames-1.jsonl，direction=ERROR）：
    #       10:01 会话 ERROR=0、cc-object-sent=3（能进）
    #       10:04 起 ERROR=140~292、cc-object-sent=0（全黑屏）
    #     ⭐ 修法：key 一律压成 **str**。同文件下面 ``unhandled`` 用的就是
    #       ``f"{command}:{selector}"``，那是**一直正常**的现成范本 —— 照抄它。
    #       另：``session["ground"]``（controls.py）是 str-key dict 且长期正常，
    #       同样证明「str key 安全、tuple/int key 炸」。
    seenKeys = flow.session.setdefault("uplinkKeys", {})
    upKey = "%d:%d" % (command, selector)
    if upKey not in seenKeys and len(seenKeys) < 128:
        seenKeys[upKey] = True
        tracelog.emit("uplink", flow.session.get("scene", ""),
                      "cmd=%d sel=%d bodyLen=%d hex=%s"
                      % (command, selector, len(body), body[2:34].hex()))
    if command == 0x1D and selector == 1:
        flow.send(command, wire.heartbeat(body), "heart-beat-response")
        return
    if command == 8 and selector == 9:
        wire.exact(body, 3, "unify-time")
        last = flow.session.get("unifyAt")
        if last is None or flow.now - last >= 250:
            rtt = 0 if last is None else min(60000, max(1, flow.now - last))
            # ⭐⭐ 2026-09-20（第十一轮）：``svr_time`` 是**服务器 epoch 毫秒**，
            #    不是原生层的单调时钟。``CS_PROTO_UNIFY_TIME_RSP { svr_time, rtt_time }``
            #    （TieJiClient.exe 的 TDR metalib ``sh_proto_cs`` 解出），
            #    旧写法发 ``flow.now``（约 4.6 天量级）⇒ 客户端换算出 1970-01-05。
            flow.send(8, struct.pack(">HQQ", 10, epochMs(), rtt), "unify-time-rsp")
            flow.session["unifyAt"] = flow.now
        return
    if command == 26 and selector == 1:
        # ⭐⭐⭐ 2026-09-20（第十一轮，TDR 权威定案）：``SH_CS_CMD_HALL_TIME = 26``。
        #
        #    ★ 根因：前几轮一直把「大厅时间回包」发在 **27** 上，而 27 其实是
        #      ``SH_CS_LOGIC_QUEST``。客户端从未收到过大厅时间 ⇒ 时间基准恒为 0
        #      ⇒ 无论服务端发什么值、本机时钟怎么调，一律判「此模式还未到开放时间」
        #      （文案取「**还未到**」而非「已结束」，正是基准为 0 的特征）。
        #
        #    证据来源（不再靠「符号表序号 + 3」这类启发式猜测）：
        #      ``TieJiClient.exe`` 内嵌 5 个 TDR metalib（magic
        #      ``d6 02 0b 00 20 00 00 00``，文件偏移 0xE39250 / 0x10AA018 /
        #      0x11A1008 / 0x15D9958 / 0x15DEAD0），其中 ``sh_proto_cs`` 可直接用
        #      本仓库 ``scripts/codec/tdr.py`` 解析。实测解出：
        #          SH_CS_CMD_HALL_TIME   = 26
        #          SH_CS_CMD_HEART_BEAT  = 29   ← 与本文件 0x1D 分支一致
        #          SH_CS_CMD_ROOM        = 30   ← 与 codec/room_flow.py 0x001E 一致
        #          SH_CS_CMD_UNIFY_TIME  = 8    ← 与上面 unify-time 分支一致
        #          SH_CS_CMD_LOCAL_PKG   = 60
        #          SH_CS_LOGIC_QUEST     = 27   ← 之前被误当成时间通道
        #          E_CS_PROTO_HALL_TIME_REQ = 1 / _RSP = 2
        #          CS_PROTO_HALL_TIME_REQ = { char data[1] }
        #          CS_PROTO_HALL_TIME_RSP = { uint64 hall_time_ms }
        #      ⇒ 回包 = selector(u16 大端, =2) + u64 毫秒，共 10 字节，命令号 26。
        hall_now = epochMs()
        flow.send(26, struct.pack(">HQ", 2, hall_now), "hall-time-rsp")
        flow.result["logs"].append(
            "hall-time-rsp(cmd=26) t=" + str(hall_now)
            + " reqHex=" + body[2:12].hex())
        return
    if command == 27 and selector == 1:
        # ⭐⭐⭐ 2026-09-20（第十二轮，实测推翻旧结论）：
        #
        #    **这个分支根本不是「大厅时间」。** 证据来自服务端自己的抓包
        #    （``data/12980-5988330/wire/frames-1.jsonl``，22:27 那次真实启动）：
        #
        #    客户端在 ``command=27 / sel=1`` 上发的 body 恒为 6 字节，形状是
        #        ``sel(2) + u32 id``，id 取值实测为
        #        4, 27, 28, 30, 43, 52, 67, 72, 86, 106 …
        #    这是**一批资源/配置 ID 的逐条查询**，不是"问现在几点"。把它当时间回包
        #    （旧代码）只会让服务端单方面刷出 621 条 ``hall-time-rsp`` + 552 条
        #    ``hall-time-REPEAT``，纯噪音，且从未解决问题。
        #
        #    另外两条硬事实，一起钉死"改时间没用"这个结论：
        #      · 服务端本次会话收到的 **全部** command 是
        #        1,27,30,31,33,34,36,39,40,46,53,56,59,60,66,71,73,82,88
        #        —— **从来没有 26**。所以 ``SH_CS_CMD_HALL_TIME = 26`` 那条补丁
        #        打不打都一样，客户端不会在 26 上问时间。
        #      · ``SH_CS_CMD_MATCH = 32``（TDR 权威值）在整场会话里**一次都没出现**。
        #        你点「会战模式」→ 点 ``singleMatch`` 的同一秒，客户端**一个包都没发**
        #        ⇒ 拦截发生在客户端本地、且在构造 32 号包之前。
        #
        #    因此本分支改成**只记录明文、不主动回包**，把客户端的真实意图完整落盘，
        #    供下一轮定位使用。原「三种形状轮试」的实验代码已删除（有备份）。
        _r = flow.session.setdefault("hallTimeReqRaw", [])
        _r.append(body.hex())
        if len(_r) <= 40:
            flow.result["logs"].append(
                "cmd27/sel1-raw #" + str(len(_r) - 1)
                + " len=" + str(len(body)) + " hex=" + body.hex())

    if command == 60 and selector == 3:
        # 60 = ``SH_CS_CMD_LOCAL_PKG``，sel=3 = ``ENM_LOCAL_CMD_PING_INFO``
        #   （``..._INVALID=0 / REPORT_REQ=1 / DELAY_INFO=2 / PING_INFO=3 /
        #     TIMEOUT_INFO=4``）—— 光看名字是 7.5 秒一次的 ping 上报。
        #
        # ⚠️⚠️ 但用户实机反馈：客户端上**有「服务器时间」显示，且它不随真实时间走**，
        #   而本分支原来回的 ``now_ms`` 恰恰就是个**永不跳变的单调时钟**（4.6 天量级）
        #   ⇒ 它很可能**就是**客户端取「当前服务器时间」的地方（协议名具误导性）。
        #   所以本轮先把值修正成 epoch；两个分支都发 epoch 之后，
        #   再让用户看客户端显示的服务器时间**是否跟着变** —— 变了就能反推出
        #   客户端真正读的是哪一条，不用再猜。
        echo_payload = struct.unpack_from(">I", body, 2)[0] if len(body) >= 6 else 0
        # ⭐⭐ 2026-09-20（第七轮）：用户实机反馈「日志里的服务器时间没变」——
        #    指的就是这一行原本输出的 now_ms。原实现发的是 ``flow.now``，
        #    而它是原生层传进来的**单调时钟**（实测 401605970 ms ≈ 4.65 天，
        #    且与真实时间无关、永不"跳变"）。客户端若拿它当「当前服务器时间」，
        #    换算出来是 1970-01-05 ⇒ 永远「此模式还未到开放时间」。
        #    ⇒ 改成 epoch 毫秒（和 cmd=27 那条一致）。
        sync_now = epochMs()
        flow.send(60, struct.pack(">H I Q", 4, echo_payload, sync_now), "time-sync-rsp")
        flow.result["logs"].append("time-sync-rsp sent now_ms=" + str(sync_now))
        return
    if scene.message(flow, command, selector, body):
        return
    # ⭐ 2026-10-03（第十七轮）：cmd=67 = SH_CS_CMD_DUNGEON（关卡协议）。
    #    sel=1 = CHANGE_STATE_REQ（训练关改变状态请求）⇒ 回 STATE_NOTIFY 回显。
    #    接线前它落到下面的 unhandled 兜底（服务端零响应）。证据见 dungeon.py。
    if dungeon.message(flow, command, selector, body):
        return
    if controls.message(flow, command, selector, body):
        return
    # ⭐ 2026-09-23：C 键上/下马（``cmd=4 sel=12/13``）。接线前它落到下面的
    #    unhandled 兜底（``unhandled command=4 selector=13``）—— 客户端骑马按 C
    #    明明发了「下马通知包」，服务端却完全没反应。证据见 controls.mountCommand。
    if controls.mountCommand(flow, command, selector, body):
        return
    # ⭐ 2026-09-20：cmd=18 = SH_CS_CMD_MO（地图物件）。「按 C 扶起/拆除云梯」
    #    走的就是这条。改动前它落到下面的 unhandled 兜底（服务端零响应）。
    if mo.message(flow, command, selector, body):
        return
    if battle.message(flow, command, selector, body):
        return
    # ⭐ 2026-09-25：cmd=9 = HAVOK 物理组。飞戟松手后客户端发 sel=7「飞行物初始
    #    姿态请求」，服务端不回 sel=8 就没有飞行物本体（证据见 battle.flyMessage）。
    # ⭐ 2026-10-03：同一组里还有 sel=3「转腰请求」（拉弓瞄准/投掷时高频发送，
    #    全量 507 条、此前全部落 unhandled），现已回 sel=4/5/6（见 battle.TURN_WAIST_REQ）。
    if battle.flyMessage(flow, command, selector, body):
        return
    # ⭐ 2026-09-26：cmd=4 sel=5 = BATTLE_HIT。客户端弹体自撞后报上来，
    #    服务端补发 sel=11 清掉该弹体、腾出坑位（证据见 battle.hitMessage）。
    if battle.hitMessage(flow, command, selector, body):
        return
    # ⭐ 2026-09-25：cmd=5 = WEAPON（换武器/武器槽切换）。
    #    sel=1 = CHANGE_WEAPON_REQ（C→S，body = sel(2) + slot(i16 大端)），
    #    slot 取值：1=主武器 / 2=副武器 / 3=辅助武器（E_CS_CHANGE_WEAPON_INDEX）。
    #    服务端必须回 CHANGE_WEAPON_RSP（sel=2，12 字节）：
    #      err_no(i32) + right_hand_weapon_tid(i32) + left_hand_weapon_tid(i32)。
    #    证据：TieJiClient.exe 内嵌 TDR metalib sh_proto_cs 解出。
    #    ⚠️ 2026-09-27 更正：cmd=5 **只管英雄主/副/辅助武器换装**（槽位 1/2/3）。
    #    投石车/云梯「按 C 上器械」走的是 **MO 组** `CS_PROTO_MO_INTERACT`(cmd23)
    #    → `mo._handleInteract` → `siege.onInteract`（不是这条）。
    #    ⚠️⚠️ 2026-09-28 再更正（本条上句里「投石车没有 7 位武器 tid」**是错的**）：
    #    投石车**有**武器 tid，只是不在 hero_roster.json 里 —— 它是「器械类武器」，
    #    登记在客户端武器模板表（`data1.vfs` 块 **125289** 起，行宽 2829、1016 行、
    #    **行首 u32 = tid**）里：**29001 = 投石车**（类型/类别 = 投石车/攻城）；
    #    同族 29002=云梯、30004/30008=弩机；另有 49001..49004 同内容变体。
    #    校验：同表 1030411=龙牙刀 / 1080311=飞戟 / 1080511=竹烟筒 与名册逐条吻合。
    #    ⇒ 两条链**都要**：MO 组负责「车进入被操控态」，cmd=5 负责「把人物换成
    #    器械类武器」——后者才是客户端挑 `投石车.psheet`（其 `待机` →
    #    `武将_投石车_待机` → `挂接器械("Man")`）的前提。见 `siege.sendCatapultWeapon`。
    #    原「`器械类型=14` = MO type，攻城器械.psheet 里 `武器id` 全为 0」这条观察
    #    仍然成立，只是它**推不出**「没有武器 tid」——`武器id` 字段本来就没被用。
    #    ⚠️ 2026-09-25 01:1x 实测：回包后客户端 WASD 失效，疑似需要额外下发
    #    VISION/武器属性数据，或 tid 必须是角色装备栏里的真实值。
    #    当前用赵云的武器 21031 测试，后续换成器械类武器 tid。
    # 换武器请求（按 1/2/3）。回**请求槽位那把真武器**的 tid。之前回 0 /
    # 1060011 / 21031 这些不属于任何槽的 tid，客户端一律无视。查不到就回 0。
    # ⚠️⚠️ 2026-10-05 实机取证：这里原本取 `wire.HERO_ID`（**启动时** ini 那张），
    #    而进图实体/视野那条链取的是**玩家选将选中**的那张 ⇒ 选姜维进图是姜维
    #    （608B 实体包里有姜维的枪和马），按 1/2/3 却回赵云的三把（会话
    #    3880-1299637081 wire 逐条核过）。改成 `scene.heroIdOf(flow)`，两条链同一个将。
    if command == 5 and selector == 1:
        if len(body) < 4:
            flow.result["logs"].append("weapon-change-req-too-short len=%d" % len(body))
            return
        slot = struct.unpack_from(">h", body, 2)[0]
        weaponHero = scene.heroIdOf(flow)
        held = wire.battleLoadout(weaponHero)[0]
        slot_tids = {entry[0]: entry[1] for entry in held}
        right_tid = slot_tids.get(slot, 0)
        left_tid = 0
        # CHANGE_WEAPON_RSP: selector(u16 大端=2) + err_no(i32) + right_tid(i32) + left_tid(i32)
        rsp_body = struct.pack(">Hiii", 2, 0, right_tid, left_tid)
        flow.send(5, rsp_body, "change-weapon-rsp slot=%d" % slot)
        flow.result["logs"].append("weapon-change-rsp sent slot=%d right=%d left=%d" % (slot, right_tid, left_tid))
        # RSP 只是「准许」，客户端真正执行切换靠随后这包 WEAPON_USE_UPDATE
        # （cmd=5 sel=3，官方语义：只有当前槽 is_using=1）。2026-09-25 实测：
        # 只回 RSP → 按 2/3 无声无息不切；三把全 is_using=1 → 客户端预检直接
        # 报「无法切换武器」。所以这里先 setCurrentSlot 再下发新状态。
        if right_tid != 0:
            wire.setCurrentSlot(slot)
            flow.send(5, wire.weaponUseUpdate(weaponHero), "weapon-use-update slot=%d" % slot)
            flow.result["logs"].append("weapon-use-update sent slot=%d" % slot)
            # 客户端的姿势/输入门禁跟人物状态走：不下发这一拍，换完武器人物还
            # 停在旧状态（拿武器姿势不对、投掷输入被本机挡掉）。1=ACT_STATE_
            # CHANGE_WEAPON，走和攻击链同一条 STATE_SYNC_SIMPLE 通道。
            battle.syncState(flow, battle.CHANGE_WEAPON_STATE,
                             "weapon-change-state-push slot=%d" % slot)
            flow.result["logs"].append("weapon-change-state-push sent slot=%d" % slot)
        return
    if command == 5 and selector == 7:
        # E_CS_PROTO_WEAPON_CHANGE_STATE=7 cli->svr「武器切换使用状态」= X 键的模式
        # 切换请求。2026-09-25 实测按 X 客户端**没发**这条（被本机弹药门禁挡着）。
        # 先只记字节，回什么等它真出现再定，不猜。
        flow.result["logs"].append("weapon-change-state-req len=%d hex=%s"
                                   % (len(body), body.hex()))
        return
    if flow.session["role"] == "logic":
        message = Method3UplinkMessage(event["sequence"], command, event["serverTimeMs"], body)
        responses = login_flow.build_login_flow_responses(
            message, include_fixed_battle_hero=True,
            hero_cards=wire.HERO_CARDS, hero_souls=wire.HERO_SOULS,
            # ⭐⭐ 2026-10-01（第十六轮）：**必须是现算值，不能用加载时常量**。
            #    `HERO_BATTLE_FORMATION` 在 import 时算一次，那时 `_ACTIVE_HERO`
            #    是 `level.ini` 里的 3031（华佗）⇒ 大厅下发的阵容第 1 格永远是华佗，
            #    而客户端把**阵容第 1 格**当「本地英雄」并锁定到进图
            #    ⇒ 按关卡换将（`HERO_BY_LEVEL`）在进图侧生效了，界面上仍显示华佗。
            #    实机实证（会话 44700-957621011）：`20:47:20 room-create level=10023`
            #    切图成功、`20:49:09 cmd=34 sel=3` 才来查阵容 —— **请求发生在切图之后**，
            #    此时 `setHeroForLevel()` 已把 `_ACTIVE_HERO` 改成 1101，故现算即可拿到赵云。
            battle_heroes=wire.battleFormationNow(),
            hero_weapons=wire.HERO_CARD_WEAPONS,
            container_card_limit=wire.HERO_CONTAINER_LIMIT,
            unlocks=wire.HERO_UNLOCK_BY_ID)
        for response in responses:
            flow.send(response.command_id, response.body, response.reason)
            if response.reason not in flow.session["hydration"]:
                flow.session["hydration"].append(response.reason)
            flow.phase("lobby-data-sent")
        if (any(r.reason == "hero-card-item-container" for r in responses)
                and "heroPushQueue" not in flow.session):
            # 首包之外的卡排队延时推（见 handleTimer 的 hero-card-push）。
            # 每个连接只排一次：客户端重试 GET_CONT 不该再来一轮。
            flow.session["heroPushQueue"] = [list(c) for c in
                                             wire.HERO_CARDS[wire.HERO_CONTAINER_LIMIT:]]
            if flow.session["heroPushQueue"]:
                flow.later("hero-card-push", 400)
            else:
                flow.session.pop("heroPushQueue", None)
        if responses or (command == 0x1E and handleRoom(flow, body, selector)):
            return
        if command == 0x20 and selector == 1:
            # ⭐ 2026-09-19：match-start 路径**也**走自动切图。
            # 新手向导的「团队模式」走的就是这条；body 是 opaque 的，
            # 但里面塞了卡片 ID（pattern_level_map.csv 的 pattern_id）。
            # 扫一遍命中已知 pattern 就切图 —— 用户在新手向导点什么进什么。
            wire.probeMatchPattern(body, flow.result["logs"])
            # ⚠️ 2026-09-19 补：切图后必须同步 session["scene"]。
            #    room-create 分支有这一句，match-start 分支漏了 → 空气墙/高度场
            #    还按**模块加载时**的旧场景（pve_gc）去查，樊城(tszz)的高度场
            #    永远加载不到（实测 ground-z-follow 触发 0 次）。
            try:
                flow.session["scene"] = wire.sceneFor()
            except (AttributeError, TypeError):
                pass
            flow.send(0x20, wire.matchStart(body), "match-start-result-zero")
            flow.later("match-result", 1000)
            flow.later("match-enter", 2000)
            flow.phase("matching")
            return
    observed = flow.session.setdefault("unhandled", {})
    key = f"{command}:{selector}"
    if key not in observed and len(observed) < 64:
        observed[key] = True
        flow.result["logs"].append(f"unhandled command={command} selector={selector}; no speculative response")
    # ⭐ 2026-09-19：把**解密后**的载荷记到日志，用于反推协议格式。
    #    ``event["body"]`` 是原生层解密完的明文（selector 就是它的前两字节），
    #    但日志里原本只留了 command/selector，等于把最有价值的部分丢了。
    #    ⚠️ 2026-09-20 更正：以前注释写的「C 键 = cmd=16 sel=3（伴随 60/2）」是
    #    **错的** —— cmd=16 sel=3 实测是 60 秒一次的周期上报（bodyLen=30 一串
    #    float，角色状态同步），跟按 C 无关。真正的按 C 协议是
    #    ``SH_CS_CMD_MO = 18``（见 scripts/mo.py 顶部），已接线。
    #    每个 command 只记前 3 次，避免刷爆日志。
    dumped = flow.session.setdefault("unhandledBodyCount", {})
    seen = dumped.get(key, 0)
    if seen < 3 and len(body) > 2:
        dumped[key] = seen + 1
        flow.result["logs"].append(
            "unhandled-body cmd=" + str(command) + " sel=" + str(selector)
            + " bodyLen=" + str(len(body))
            + " hex=" + body[2:66].hex())


def sanitizeState(node):
    """把 state 里**非 str 键的 dict** 就地压成 str 键；返回修好的键数。

    ⚠️⚠️ 为什么必须有这道兜底（2026-10-03 第二次踩同一个坑）
    ------------------------------------------------------
    原生宿主序列化会话 state 时**只认 str key 的 dict**；遇到 int / tuple key
    会抛 ``unsupported state type: dict`` 并**丢弃整条消息** ⇒ 那条上行报文
    在服务端等于没到。症状取决于被丢的是哪条报文：

    * 2026-09-24：``upKey`` 用 tuple ⇒ 丢的是**进图报文** ⇒ **黑屏进不去**；
    * 2026-10-03：``npc_battle`` 的 ``npcHp``/``npcDead`` 用 ``mid``(int)
      ⇒ 丢的是**挥砍报文** ⇒ 靠近 NPC 挥砍**卡住出不来**（离远不命中、
      不建 dict，所以一切正常）。

    两次都靠人工翻 ``wire/frames-1.jsonl`` 的 ``direction=ERROR`` 才发现。
    这里在 ``handleEvent`` 唯一的出口做一道兜底：**任何**非 str 键都压成 str，
    并落盘一条 ``state-sanitized`` 告警 —— 让这类 bug 最多只是「键名变形」，
    再也不会静默丢掉整条消息。

    ⚠️ 兜底**不是**写 int 键的许可证：键压成 str 后，用 int 键去读的代码会读不到。
    新代码一律**直接写 str 键**。
    """
    fixed = 0
    if isinstance(node, dict):
        for key in [k for k in node if not isinstance(k, str)]:
            node[str(key)] = node.pop(key)
            fixed += 1
        for value in node.values():
            fixed += sanitizeState(value)
    elif isinstance(node, list):
        for value in node:
            fixed += sanitizeState(value)
    return fixed


def handleEvent(event, state, context):
    state = deepcopy(state)
    flow = Flow(event, state, context)
    eventType, key = event["type"], str(event["connection"])
    if eventType == "operator":
        if event.get("name") != "diagnostic":
            raise ValueError("only research:diagnostic is implemented; no unverified correction bundle")
        flow.result["logs"].append(f"diagnostic phase={state['phase']} sessions={len(state['sessions'])} "
                                   f"baseline={wire.BASELINE_ID} scene=1028 actor=1 heroes={wire.HERO_IDS} "
                                   f"ground={wire.GROUND_STEP_DISTANCE}/{wire.GROUND_STEP_MS}ms "
                                   "prime=state10-state1 outcome=none clientAcceptance=unverified")
        for connection, session in list(state["sessions"].items())[:32]:
            flow.result["logs"].append(
                f"connection={connection} role={session['role']} phase={session.get('phase', 'waiting')} "
                f"actorImported={session.get('actorImported', False)} heroChosen={session.get('heroChosen', False)} "
                f"battleEntered={session.get('battleEntered', False)} pending={list(session.get('pending', {}))}")
    elif eventType == "connected":
        if event["role"] not in ("login", "logic", "instance") or key in state["sessions"]:
            raise ValueError("invalid or duplicate business connection")
        state["sessions"][key] = {"role": event["role"], "hydration": [], "pending": {}, "camp": 1,
                                  "authenticated": False}
        # ⚠️ 2026-09-19：三次实机失败都伴随**一个空会话目录**（连握手都没有）。
        # 客户端到底开了几条连接、各是什么角色，以前只能靠目录猜。
        # 这条让「实例连接到底起来没有」直接可读。
        flow.result["logs"].append(
            f"connection-opened connection={key} role={event['role']}"
            f" sessions={len(state['sessions'])}"
            f" roles={sorted({s['role'] for s in state['sessions'].values()})}"
            f" phase={state['phase']}")
    elif eventType == "closed":
        state["sessions"].pop(key, None)
        if not state["sessions"]:
            state["phase"] = "waiting"
        elif not any(session["role"] == "instance" for session in state["sessions"].values()):
            state["phase"] = "lobby-data-sent"
    elif flow.session is not None:
        if eventType == "authenticated":
            if not flow.session.get("authenticated"):
                flow.session["authenticated"] = True
                name, delay = {"login": ("version", 25000), "logic": ("login", 25000),
                               "instance": ("instance-init", 20)}[flow.session["role"]]
                flow.later(name, delay)
        elif eventType == "timer":
            handleTimer(flow, event["name"])
        elif eventType == "message":
            if flow.session.get("authenticated", True) is not True:
                raise ValueError("message before authentication")
            handleMessage(flow, event)
        else:
            raise ValueError(f"unknown business event: {eventType}")
    # ⭐ 2026-10-03：唯一出口的兜底 —— 非 str 键一律压成 str（否则原生层丢整条消息）。
    fixed = sanitizeState(flow.result["state"])
    if fixed:
        message = ("state-sanitized non-str-dict-keys=%d（原生层只认 str key，"
                   "写 int/tuple 键会丢整条报文）" % fixed)
        flow.result["logs"].append(message)
        try:
            tracelog.emit("state", flow.session.get("scene", "") if flow.session else "",
                          message)
        except Exception:  # noqa: BLE001 —— 告警本身绝不许影响主链
            pass
    return flow.result
