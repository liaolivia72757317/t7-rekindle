"""Request-driven fixed instance assembly; native timers survive script reload."""
import struct

from . import ccobject, contracts as wire, controls, dungeon, mo, npc, npc_ai, tracelog, tutorial
from .codec import audio_flow, battle_flow, leave_flow, login_flow, room_flow, vision_flow


def heroIdOf(flow):
    """本会话实际使用的武将 —— **唯一取将入口**，并顺手同步回 ``session``。

    优先级：
      ① **玩家自己选的**（``session["heroPicked"]``，由 ``0x36/0x32`` 选将写入）；
      ② **按关卡自动切换**的当前武将（``wire.activeHeroId()`` → ``contracts._ACTIVE_HERO``）。

    ⚠️⚠️ 2026-10-03（第十六轮）：**绝不能再回落到 ``wire.HERO_ID``**。
    ---------------------------------------------------------------
    旧代码三处写 ``flow.session.get("heroId", wire.HERO_ID)``，而
    ``setdefault("heroId", wire.HERO_ID)`` 会先把 ini 里的固定值（3031 华佗）
    冻进 session ⇒ ``actorVision()`` 收到**显式** heroId 就**跳过**它自己的
    ``_ACTIVE_HERO`` 默认值 ⇒ 发出去的永远是华佗；而 ``actorInfo()`` 不传
    heroId、走的是 ``_ACTIVE_HERO`` ⇒ 基础信息说许褚、视野实体却是华佗。
    客户端按**视野实体**建人 ⇒ 实机就是「换将没生效，模型一直是华佗」。

    ⚠️ 顺手把解析结果写回 ``session["heroId"]`` 是**必须的**：
    ``controls.py`` 有 6 处直接读 ``flow.session.get("heroId", 0)`` 判断
    「这个将带不带马」（``heroHasMount`` / ``battleLoadout``），不同步会让
    骑兵图判定错。写回**只在非玩家自选时**做，不覆盖玩家选将结果。
    """
    if not flow.session.get("heroPicked"):
        flow.session["heroId"] = wire.activeHeroId()
    return flow.session.get("heroId") or wire.activeHeroId()


def sendCcObjects(flow, force=False):
    """把本场景的 CC 攻城器械（云梯 / 攻城车 / 投石车 / 屋顶…）推给客户端。

    ⚠️ **独立发包**，既有 ``actorVision`` 一个字节都不动（那是回归底线）。
    见 ``ccobject.py`` 顶部：器械是**附加**能力，读盘/编码失败只记日志不抛 ——
    宁可少几个器械，也不能让人进不去图。

    开关：``T7_CC_OBJECT``（见 ``ccobject.ccObjectLimit``）。

    ⚠️ 按**场景**去重：``battleEntry`` 与 ``camp-choice-ok`` 两条路都会调本函数，
    同一局里只发一次。换图（scene 变了）会重发；
    ``battleEntry`` 里那条 ``vision_del_event`` 会把去重标记清掉（见那里）。

    ``force=True`` 跳过去重 —— 2026-09-20 加。理由见 ``visionObjectMids``：
    客户端是**收到 VISION_LIST_RSP 之后**才有索引表，在此之前送到的 CC 物件
    没有槽位可挂（实机取证：CC 报文比索引表早 50 条记录 / 7.9 秒）。
    """
    scene = controls.airWallScene(flow)
    if not force and flow.session.get("ccObjectsSent") == scene:
        return
    try:
        body = ccobject.visionEvent(scene, wire.serverNowMs())
    except (OSError, ValueError, KeyError, TypeError) as error:
        msg = "cc-object-failed scene=" + repr(scene) + " error=" + repr(error)
        flow.result["logs"].append(msg)
        tracelog.emit("cc", scene, msg)
        return
    if body is None:
        return
    flow.session["ccObjectsSent"] = scene
    msg = "cc-object-sent scene=" + repr(scene) + " bytes=" + str(len(body)) \
          + " " + ccobject.describe(scene)
    flow.result["logs"].append(msg)
    tracelog.emit("cc", scene, msg)
    flow.send(0xE, body, "instance-cc-dynamic-vision-add-event")
    # ⭐ 2026-09-20：物件挂上去了，紧接着把它们的 **MO 状态** 也推一份。
    #    ADD_EVENT 里带的 state 已不再是 0，但客户端只有在收到
    #    ``update_mo_state``（cmd=18 msg_id=3）时才会真正建立交互上下文 ——
    #    否则按 C 会被本地的 ``E_MO_INTERACT_PRE_CHECK_MO_STATE`` 前置检查吞掉。
    #
    # ⚠️ **必须包 try/except**：2026-09-20 事故 —— pushStates 往 session 里写了
    #    int-key dict，原生层序列化抛 ``unsupported state type: dict``，
    #    这条消息被整条丢弃 ⇒ 樊城攻城模式**进不去图**。
    #    器械是**附加**能力，坏了绝不能再拖垮进图流程（同 ccobject 纪律）。
    try:
        mo.pushStates(flow, "after-cc-objects")
    except Exception as error:
        flow.result["logs"].append("mo-push-crashed error=" + repr(error))


def sendTutorialObjects(flow, force=False):
    """把本场景的**教学引导物件**（地面箭头 / 流光 / 灯笼 / 靶子）推给客户端。

    ⭐ 2026-09-30 新增（用户选定「方案 A 引导版」）。与 ``sendCcObjects`` 同一套
    纪律：**独立发包**、器械/引导任一坏掉只记日志不抛 —— 宁可少几个箭头，
    也不能让人进不去图。用**同一个编码器**
    ``encode_cc_dynamic_vision_add_event``（樊城 24 件器械已实机跑通那条路）。

    数据源 ``data/scene/<场景>/tutorial_guide.json``（**多数场景没有这份文件**
    ⇒ 直接返回，行为逐位不变）。开关见 ``tutorial.guideLimit``（``[guide]`` 段）。

    ⚠️ 同样按**场景**去重：``battleEntry`` 与 ``camp-choice-ok`` 两条路都会调本函数。
    """
    scene = controls.airWallScene(flow)
    if not force and flow.session.get("guideObjectsSent") == scene:
        return
    try:
        body = tutorial.visionEvent(scene, wire.serverNowMs())
    except (OSError, ValueError, KeyError, TypeError) as error:
        msg = "guide-object-failed scene=" + repr(scene) + " error=" + repr(error)
        flow.result["logs"].append(msg)
        tracelog.emit("guide", scene, msg)
        return
    if body is None:
        return
    flow.session["guideObjectsSent"] = scene
    msg = "guide-object-sent scene=" + repr(scene) + " bytes=" + str(len(body)) \
          + " " + tutorial.describe(scene)
    flow.result["logs"].append(msg)
    tracelog.emit("guide", scene, msg)
    flow.send(0xE, body, "instance-guide-dynamic-vision-add-event")


def sendNpcObjects(flow, force=False):
    """把本场景**关卡自己的 NPC**（训练场陪练 / 城镇人 / 刷怪组单位）推给客户端。

    ⭐ 2026-10-03 新增（用户三条诉求第 1 条「接关卡自己的 NPC 通道」）。与
    ``sendCcObjects`` / ``sendTutorialObjects`` 同一套纪律：**独立发包**、读盘或
    编码失败只记日志不抛 —— 宁可少几个 NPC，也不能让人进不去图。

    与前两条通道的区别：CC 器械与教学引导走 ``encode_cc_dynamic_vision_add_event``
    （MO 物件），而 NPC 是**带模型的 actor**，必须走
    ``encode_fixed_local_actor_vision_add_event``（本地玩家那条同款编码器）——
    每个 NPC 一条 ADD_EVENT。

    数据源 ``data/scene/<场景>/npc_actors.json``（**多数场景没有这份文件**
    ⇒ 直接返回，行为逐位不变）。开关见 ``npc.npcLimit``（``[npc]`` 段）。

    ⚠️ 同样按**场景**去重：``battleEntry`` 与 ``camp-choice-ok`` 两条路都会调本函数。
    """
    scene = controls.airWallScene(flow)
    if not force and flow.session.get("npcObjectsSent") == scene:
        return
    try:
        bodies = npc.visionEvents(scene, wire.serverNowMs(),
                                  player_camp=flow.session.get("camp"))
    except (OSError, ValueError, KeyError, TypeError) as error:
        msg = "npc-object-failed scene=" + repr(scene) + " error=" + repr(error)
        flow.result["logs"].append(msg)
        tracelog.emit("npc", scene, msg)
        return
    if not bodies:
        return
    flow.session["npcObjectsSent"] = scene
    for body in bodies:
        flow.send(0xE, body, "instance-npc-dynamic-vision-add-event")
    msg = "npc-object-sent scene=" + repr(scene) + " count=" + str(len(bodies)) \
          + " bytes=" + str(sum(len(b) for b in bodies)) \
          + " " + npc.describe(scene) \
          + " player-camp=" + str(flow.session.get("camp"))
    flow.result["logs"].append(msg)
    tracelog.emit("npc", scene, msg)
    # ⭐ 2026-10-04：发完 NPC 起 AI 控制器（门控 T7_NPC_AI，默认关）。
    npc_ai.start(flow)


def visionObjectMids(flow):
    """``VISION_LIST_RSP`` 要登记的 mid 列表：自己 + 本场景 CC 器械 + 教学引导。

    ⚠️ **CC 的 mid 必须进来** —— 2026-09-20 实机取证（会话 13496-374094473）：

      客户端 ``TieJiClient.exe`` / metalib ``sh_proto_cs`` 的 TDR 声明：
        ``CS_PROTO_VISION_VISION_LIST_RSP.obj_mids`` : uint64[]
        ``CS_PROTO_VISION_CC_DYNAMIC_INFO.rid``      : uint64   ← 同一个东西
        ``CS_PROTO_VISION_GET_OBJECTS_REQ.obj_mids`` : uint64[]

      客户端**按 obj_mids 建索引表，再用 rid 挂载对象**。
      改动前这里只回 ``(1, 2)``（自己 / 敌人），24 个 CC 物件的 rid 不在表里
      ⇒ 客户端收到 4981 字节的 CC 报文后**无处挂载 ⇒ 整包丢掉**
      ⇒ 门 / 门机关 / 云梯 / 攻城车全都看不见。

    ⭐ 教学引导物件（rid 20001 起）同理，必须登记，否则箭头看不到。

    ⭐⭐ 2026-10-01（第二十轮）：**mid=2「敌人」已从本表移除**。
      那是一个从最初版本（2026-09-20 的 ``data/.../t7rev_f13a4bcc...``）就带进来的
      写死占位：``enemyVision()`` 恒定下发 ``actor_mid=2 / user_id=10001 /
      hero_resource_id=110003（关羽） / actor_name=b"bot"``。它当初只是为了「视野里
      有个第二人」把链路跑通，**不是一个真实敌人**。后果是每张图进图后，除了你自己
      还会多站一个绿甲关羽、头顶标着 ``bot`` —— 训练场里尤其扎眼（用户 2026-10-01
      实机反馈）。
      本服务端是**单机练习服**，没有服务端驱动的敌人 AI：真正的敌人应当由 CC 器械 /
      关卡自己的 NPC 通道承载（那些 rid 本来就在下面 ``ccobject.objects()`` 里），
      不该由这个常量顶替。故这里不再登记 2，``sendVisionObject()`` 里对应的分支
      也已删除 —— 客户端不会再请求它，也就不会再多出一个人。

    读盘失败只记日志、退回 ``(ACTOR_ID,)`` 自己那张 —— 与 ``sendCcObjects`` 同一套纪律。
    """
    mids = [wire.ACTOR_ID]
    scene = controls.airWallScene(flow)
    try:
        mids.extend(obj.rid for obj in ccobject.objects(scene))
    except (OSError, ValueError, KeyError, TypeError) as error:
        msg = "cc-object-mids-failed scene=" + repr(scene) + " error=" + repr(error)
        flow.result["logs"].append(msg)
        tracelog.emit("cc", scene, msg)
    try:
        mids.extend(tutorial.mids(scene))
    except (OSError, ValueError, KeyError, TypeError) as error:
        msg = "guide-object-mids-failed scene=" + repr(scene) + " error=" + repr(error)
        flow.result["logs"].append(msg)
        tracelog.emit("guide", scene, msg)
    try:
        mids.extend(npc.mids(scene))
    except (OSError, ValueError, KeyError, TypeError) as error:
        msg = "npc-object-mids-failed scene=" + repr(scene) + " error=" + repr(error)
        flow.result["logs"].append(msg)
        tracelog.emit("npc", scene, msg)
    return tuple(mids)


def sendVisionObject(flow, mid):
    """按 mid 回一个视野对象（``VISION_GET_OBJECTS_REQ`` 用）。

    返回 ``True`` 表示 mid 认得；``False`` 表示不认识（调用方决定怎么处理）。
    """
    if mid == wire.ACTOR_ID:
        flow.send(0xE, wire.actorVision(flow.session["camp"], heroIdOf(flow),
                                        withMount=bool(flow.session.get("battleEntered"))),
                  "instance-fixed-local-actor-vision-add-event")
        return True
    scene = controls.airWallScene(flow)
    try:
        obj = ccobject.byRid(scene).get(mid)
    except (OSError, ValueError, KeyError, TypeError) as error:
        msg = "cc-object-by-rid-failed scene=" + repr(scene) \
              + " mid=" + str(mid) + " error=" + repr(error)
        flow.result["logs"].append(msg)
        tracelog.emit("cc", scene, msg)
    else:
        body = ccobject.visionEventFor((obj,) if obj is not None else (), wire.serverNowMs())
        if body is not None:
            flow.send(0xE, body, "instance-cc-dynamic-vision-add-event-by-mid")
            # 同 sendCcObjects：物件挂上去了就顺带把 MO 状态推过去（同样要防它拖垮回包）。
            try:
                mo.pushState(flow, mid, "by-mid")
            except Exception as error:  # noqa: BLE001
                flow.result["logs"].append("mo-push-crashed error=" + repr(error))
            return True
    # ⭐ 2026-09-30：教学引导物件（rid 20001 起）—— CC 里没有就查它。
    try:
        guide = tutorial.byRid(scene).get(mid)
    except (OSError, ValueError, KeyError, TypeError) as error:
        msg = "guide-object-by-rid-failed scene=" + repr(scene) \
              + " mid=" + str(mid) + " error=" + repr(error)
        flow.result["logs"].append(msg)
        tracelog.emit("guide", scene, msg)
        return False
    body = tutorial.visionEventFor((guide,) if guide is not None else (),
                                   wire.serverNowMs())
    if body is not None:
        flow.send(0xE, body, "instance-guide-dynamic-vision-add-event-by-mid")
        return True
    # ⭐ 2026-10-03：关卡自身 NPC（rid 30001 起）—— 前两条都没有就查它。
    try:
        actor = npc.byMid(scene).get(mid)
    except (OSError, ValueError, KeyError, TypeError) as error:
        msg = "npc-object-by-mid-failed scene=" + repr(scene) \
              + " mid=" + str(mid) + " error=" + repr(error)
        flow.result["logs"].append(msg)
        tracelog.emit("npc", scene, msg)
        return False
    if actor is None:
        return False
    flow.send(0xE, npc.encodeActor(actor, wire.serverNowMs(),
                                   player_camp=flow.session.get("camp")),
              "instance-npc-dynamic-vision-add-event-by-mid")
    return True


def begin(flow):
    flow.send(0x2C, flow.syncBody(), "instance-sync-login-rsp-after-auth")
    flow.send(0xA, wire.instanceInfo(flow.now), "instance-minimal-update-after-auth")
    flow.session["instanceStartedAt"] = flow.now
    flow.session["initialized"] = True
    flow.session["controlBaseline"] = wire.BASELINE_ID
    flow.session["groundEnabled"] = False
    flow.phase("instance-data-sent")
    flow.later("instance-game-info", 200)
    flow.later("time-sync", 2000)


def initializeBattleState(flow):
    startedAt = flow.session.get("instanceStartedAt")
    if startedAt is None:
        flow.result["logs"].append("new instance required for natural initialization; unknown instance epoch")
        return
    body = battle_flow.encode_battle_state_sync_simple(
        instance_id=wire.ACTOR_ID, seq_no=1, state=battle_flow.ACT_STATE_IDLE,
        state_change_ms=flow.now - startedAt, state_time_ms=0)
    flow.send(battle_flow.BATTLE_COMMAND, body, "instance-battle-initial-idle")


# ---------------------------------------------------------------------------
# 对局背景音乐 —— 走 ``cmd=10`` / ``sel=301`` 音效广播（编码器在 ``audio_flow``）
# ---------------------------------------------------------------------------
# 协议里**没有**「播放音乐」报文（宏名扫 ``MUSIC`` 0 命中），服务端→客户端唯一的
# 声音载体就是这条 3D 音效广播；``sound_id`` 由客户端查
# ``../data/propsheet/音效id表.psheet``（``/Music`` 段 = 60001~60033）。
# 客户端要**先有场景**才接得住音效，所以不在 ``battleEntry`` 里直发，挂延时定时器。
MUSIC_INI_SECTION = "music"
MUSIC_TIMER = "battle-music"


def _battleMusicConfig():
    """``[music]`` 段 → ``(开关, 音效号, 延时ms, 归属rid)``；缺段/坏值退回默认。

    ``battle_rid``：``self``（默认，音源挂在自己身上）| 数字（指定 rid，0 = 不给归属）。
    """
    cfg = wire.iniSection(MUSIC_INI_SECTION)

    def text(key, default):
        raw = cfg.get(key)
        return default if raw is None else str(raw).strip().lower()

    def number(raw, default):
        try:
            return int(raw)
        except ValueError:
            return default

    enabled = text("battle", "on") not in ("off", "0", "none", "")
    rid_raw = text("battle_rid", "self")
    return (enabled,
            max(0, number(text("battle_id", "60009"), 60009)),
            max(0, number(text("battle_delay", "5000"), 5000)),
            max(0, wire.ACTOR_ID if rid_raw in ("", "self")
                 else number(rid_raw, wire.ACTOR_ID)))


BATTLE_MUSIC = _battleMusicConfig()
print("[music] battle=" + ("on" if BATTLE_MUSIC[0] else "off")
      + " id=" + str(BATTLE_MUSIC[1]) + " delay=" + str(BATTLE_MUSIC[2])
      + " rid=" + str(BATTLE_MUSIC[3]) + " ini=" + MUSIC_INI_SECTION, flush=True)


def scheduleBattleMusic(flow):
    """进对局时挂背景音乐定时器（开关关 / 音效号非法就不挂）。"""
    enabled, sound_id, delay_ms, owner_rid = BATTLE_MUSIC
    if not enabled or sound_id <= 0:
        return
    flow.session["battleMusic"] = {"soundId": sound_id, "ownerRid": owner_rid}
    flow.later(MUSIC_TIMER, delay_ms)


def sendBattleMusic(flow):
    """发那条 ``sel=301`` 音效广播；只发一次，音乐循不循环由客户端事件自己决定。"""
    info = flow.session.get("battleMusic")
    if not info:
        return
    del flow.session["battleMusic"]
    scene = controls.airWallScene(flow)
    body = audio_flow.encode_trans_multi_rsp(
        server_time_ms=wire.serverNowMs(),
        sound_id=info["soundId"], owner_rid=info["ownerRid"])
    msg = ("battle-music-sent scene=" + repr(scene) + " sound=" + str(info["soundId"])
           + " path=" + (audio_flow.soundPath(info["soundId"]) or "-")
           + " rid=" + str(info["ownerRid"]) + " bytes=" + str(len(body)))
    flow.result["logs"].append(msg)
    tracelog.emit("music", scene, msg)
    flow.send(audio_flow.TRANS_COMMAND, body, "battle-music")


# ---------------------------------------------------------------------------
# ⭐⭐⭐ 2026-10-06（第十五轮，连杀语音无声根因闭环）：补发 ``INSTANCE_ENTER``
# ---------------------------------------------------------------------------
# 客户端音频 bank 加载集永远停在 psheet「模式 -1（初始音效资源）」⇒ ``Vox_System.bnk``
# / ``Weapon.bnk`` / ``Hit.bnk`` 等从未 LoadBank ⇒ 连杀/屠夫语音、挥砍、命中全无声。
# 音频集合只在两处变化：init（模式 -1）与音频侧 ``INSTANCE_UPDATE`` 游戏事件处理器
# （0x7E65C0：读事件 bag 的 ``InstanceMode`` → 按 ``InstanceTypeAudio.psheet`` 重扫）。
# 该事件由客户端实例模块处理 **E_CS_PROTO_INSTANCE_ENTER_INSTANCE**
# （cmd=0x0A sel=0x02，「sync -> client 通知副本内玩家有新玩家进入」）时以
# bag{InstanceMode, SubMode, MaxCampPlayerNum, InstanceResID, SubPattern,
# InstanceAudioEvent...} 广播 —— 私服从未发过这条 ⇒ 链路死 ⇒ bank 集永不更新。
#
# 开关 ``[audio] enter_instance``（level.ini / server.ini，默认 on）。
# ⚠️ 实验性质：payload 先用最小形态（sel + svr_time）。若客户端 TDR 期望更多
# 字段会整包丢弃（无害），届时再扩字段。
AUDIO_INI_SECTION = "audio"


def sendInstanceEnter(flow):
    """进对局后补发 ``E_CS_PROTO_INSTANCE_ENTER_INSTANCE``，驱动音频按模式重扫 bank。"""
    cfg = wire.iniSection(AUDIO_INI_SECTION)
    if str(cfg.get("enter_instance", "on")).strip().lower() in ("off", "0", "none", ""):
        return
    body = room_flow.encode_instance_enter(server_time_ms=wire.serverNowMs())
    scene = controls.airWallScene(flow)
    msg = ("instance-enter-sent scene=" + repr(scene) + " cmd=0x0A sel=0x02 bytes="
           + str(len(body)) + " （驱动客户端音频模块按 psheet 模式重扫 bank）")
    flow.result["logs"].append(msg)
    tracelog.emit("audio", scene, msg)
    flow.send(room_flow.INSTANCE_COMMAND, body, "instance-enter-instance-notify")


def battleEntry(flow):
    if flow.session.get("battleEntered") or not flow.session.get("heroChosen"):
        return
    flow.session["battleEntered"] = True
    flow.send(0x36, wire.actorState(flow.now, 6), "actor-in-scene-after-battle-confirm")
    flow.send(0xE, vision_flow.encode_vision_del_event(), "actor-vision-del-after-battle-confirm")
    # 上面这条 del 会把客户端**所有**视野物件清掉（含 CC 器械 + 教学引导），
    # 所以两个去重标记也要一起清。
    flow.session.pop("ccObjectsSent", None)
    flow.session.pop("guideObjectsSent", None)
    flow.session.pop("npcObjectsSent", None)
    flow.send(0xE, wire.actorVision(flow.session["camp"], heroIdOf(flow)),
              "actor-vision-add-after-battle-confirm")
    sendCcObjects(flow)
    sendTutorialObjects(flow)
    sendNpcObjects(flow)
    flow.send(0x36, wire.actorState(flow.now, 8), "actor-ready-play-after-battle-confirm")
    if flow.session.get("controlBaseline") == wire.BASELINE_ID:
        ground = controls.groundState(flow)
        controls.broadcast(flow, wire.POSITION, ground["heading"], 1, 0, 0,
                           "instance-ground-initial-stop")
        initializeBattleState(flow)
        controls.notifyInAir(flow, is_in_air=0)
        # 顺序有讲究：这两条都要求视野实体已经建好（上面那条 vision ADD 才有坐骑）。
        controls.activateMount(flow)
        controls.unlockOrientation(flow)
    # ⭐ 2026-10-03（第十七轮）：**训练关状态机**。非训练场静默返回 False
    #    （逐位不变）；训练场则发 STATE_NOTIFY(RUNNING) + SHOW_TRAIN_INFO。
    #    放在最后 —— 它依赖上面所有视野实体（含 NPC）已经建好。
    dungeon.enterTraining(flow)
    scheduleBattleMusic(flow)
    sendInstanceEnter(flow)
    flow.phase("battle-entry-sent")


def timer(flow, name):
    if name == "instance-init":
        begin(flow)
    elif name == "instance-game-info":
        flow.send(0xA, room_flow.encode_instance_update_game(
            server_time_ms=wire.serverNowMs(), instance_id=1), "instance-game-info")
        flow.later("instance-round-info", 200)
    elif name == "instance-round-info":
        flow.send(0xA, wire.roundInfo(flow.now), "instance-round-info")
        flow.later("instance-round-state", 200)
    elif name == "instance-round-state":
        # ⭐⭐⭐ 2026-09-21（第十四轮）：**cur 从 2 改成 1**。
        #
        #    用户实机：准备阶段「等待其他玩家…30 秒」**弹了两次**，
        #    第一次正常、第二次又来一次（且第二次读数不对）。
        #
        #    抓包证实（会话 4856-21150021）``cur=2`` 被发了**两遍**：
        #        after-auth  cur=2  dur=5400000   (16:42:13.573)
        #        prepare     cur=2  dur=30000      (16:42:21.662)  ← 客户端 load-ok 后 18ms
        #    两条都是「准备态」，客户端于是**把准备界面弹了两次**。
        #    多出来的那次就是本帧。
        #
        #    ⚠️ 本帧在客户端**刚连上、还在加载地图**时就发（比 load-ok 早约 8 秒），
        #    此时推「准备倒计时」本来就没意义 —— 真正该推的那条在
        #    ``actor-load-ok``（scene.message，command==0x36 selector==0x14）分支，
        #    那里发的 ``cur=2 + PREPARE_MS(30000)`` 才是对的、也只发一次。
        #
        #    所以这里改成 ``cur=1``（未开始/初始化中），让客户端不再弹准备界面；
        #    回合正式开始由 ``round-start`` / ``round-game`` 两条推进。
        #    ⚠️ 时长仍给 GAME_MS：部分客户端分支会拿 duration 当总时长，
        #    给 0 可能被判「已结束」。
        flow.send(0xA, wire.roundState(flow.now, 1, wire.GAME_MS), "instance-round-state-after-auth")
        flow.later("instance-start-pattern", 200)
    elif name == "instance-start-pattern":
        flow.send(0xA, wire.startPattern(), "instance-start-pattern-data-ntf-after-auth")
        flow.later("instance-spawn-area", 200)
    elif name == "instance-spawn-area":
        flow.send(0xA, wire.spawnArea(flow.now), "instance-spawn-area-state-after-auth")
        # ⭐ 2026-09-19（第二轮修正）：教学关**没有准备阶段**。
        # game_level_table.csv 里宛城的 prepare_logic / start_logic / finish_logic
        # **全是空**（只有 main_logic=_blank_btree），客户端教学流程不处理
        # round-state 的倒计时。
        # 第一轮我们在这推了 state=2 + PREPARE_MS(30s)，结果实机表现是
        # 「倒计时显示 30 但数字永远不走，玩家干等 25 秒」——用户报的就是这个。
        # 所以这里改成**直接进 GAME**：不推 prepare（那个不动的 30 就不会出现），
        # 直接 battleEntry + 推 state=4。
        # ⚠️ **只对 tutorial 生效**（isTutorial() 守卫），匹配模式照旧走
        # load-ok → prepare → start → game。
        if wire.isTutorial() and not flow.session.get("loaded"):
            flow.session["loaded"] = True
            flow.session["heroChosen"] = True
            # ⭐ 2026-10-03：这里原本写死 ``wire.HERO_ID``（ini 的 3031），
            #    会把按关卡换将（``_ACTIVE_HERO``）**冻在门外**。改成当前生效值；
            #    没切图时它 == HERO_ID，逐位不变。见 ``heroIdOf`` 的说明。
            flow.session.setdefault("heroId", wire.activeHeroId())
            # battleEntry 会读 session["camp"]；教学关正常情况下 camp 已在
            # instance-camp-choose 里设好，这里兜底避免 KeyError 把整个
            # 进图流程打断。
            flow.session.setdefault("camp", 1)
            flow.later("actor-select", 1000)
            battleEntry(flow)
            flow.send(0xA, wire.roundState(flow.now, 4, wire.GAME_MS),
                      "instance-round-state-game-tutorial")
            controls.enableGround(flow)
            flow.phase("game-sent-tutorial")
    elif name == "actor-select":
        flow.send(0x36, wire.actorState(flow.now, 5), "instance-actor-state-after-load-ok")
        flow.phase("hero-selection-sent")
    elif name == "round-start":
        if not flow.session.get("heroChosen"):
            flow.session["prepareExpired"] = True
            flow.result["logs"].append("PREPARE elapsed without hero selection; no fabricated GAME")
            return True
        battleEntry(flow)
        flow.send(0xA, wire.roundState(flow.now, 3, wire.START_MS), "instance-round-state-start")
        flow.later("round-game", wire.START_MS)
    elif name == "round-game":
        flow.send(0xA, wire.roundState(flow.now, 4, wire.GAME_MS), "instance-round-state-game")
        controls.enableGround(flow)
        flow.phase("game-sent")
    elif name == "time-sync":
        # ⭐⭐⭐ 2026-09-21（第十五轮）：**这里必须发 epoch，不能发 flow.now**。
        #   本帧每 2 秒一次，是客户端校准「服务器时间」的主时钟。
        #   发 flow.now（tconnd 单调时钟）会与 roundState/roundInfo 里的 epoch
        #   **互相打架**：客户端每 2 秒就发现服务器时间跳变 ⇒ 倒计时被反复重置
        #   ⇒ 滴答音效**一直响**（用户实机反馈）。
        flow.send(8, struct.pack(">HQQB", 11, wire.serverNowMs(), 0, 1),
                  "instance-periodic-time-sync")
        flow.later("time-sync", 2000)
    elif name == "logout":
        flow.send(0x2C, leave_flow.encode_sync_logout_response(
            server_time_ms=wire.serverNowMs(), back_data=flow.session.pop("logoutData")), "sync-logout-response")
        flow.phase("lobby-data-sent")
    elif name == MUSIC_TIMER:
        sendBattleMusic(flow)
    else:
        return False
    return True


def leave(flow, body):
    backData = leave_flow.decode_sync_logout_request(body)
    flow.cancelTimers()
    if flow.session["role"] == "instance":
        flow.send(0xA, wire.roundState(flow.now, 5, 0), "instance-round-state-end")
        flow.send(0xA, leave_flow.encode_si_finish_game(
            server_time_ms=wire.serverNowMs(), local_user_id=wire.USER_ID), "instance-finish-game")
        flow.send(0xA, leave_flow.encode_instance_leave_instance(
            server_time_ms=wire.serverNowMs()), "instance-leave-instance")
    flow.session["logoutData"] = backData & 0xFF
    flow.session["leaving"] = True
    flow.session["groundEnabled"] = False
    flow.phase("leaving")
    flow.later("logout", 250)


def message(flow, command, selector, body):
    if command == 0x2C and selector == 2:
        leave(flow, body)
        return True
    if command == 0x37 and selector == 1:
        guid, groupType = leave_flow.decode_user_group_quit_request(body)
        flow.send(0x37, leave_flow.encode_user_group_quit_response(
            user_group_guid=guid, user_group_type=groupType), "user-group-quit-response")
        flow.phase("lobby-data-sent")
        return True
    if flow.session["role"] != "instance" or flow.session.get("leaving"):
        return False
    flow.session.setdefault("camp", 1)
    if command == 0xA and selector == 0x6A:
        flow.send(0xA, wire.instanceInfo(flow.now, flow.session.get("instanceStartedAt")),
                  "instance-update-request")
    elif command == 0xA and selector == 0xB:
        placeholder = room_flow.decode_instance_choose_hero_request(body)
        flow.send(0xA, room_flow.encode_instance_choose_hero_message(
            placeholder=placeholder), "instance-enter-choose-hero")
    elif command == 0x23 and selector == 1:
        wire.exact(body, 6, "camp-choice")
        camp = struct.unpack_from(">i", body, 2)[0]
        if camp not in (1, 2):
            raise ValueError("camp-choice requires camp 1 or 2")
        flow.session["camp"] = camp
        # 客户端报了 camp 才换边：樊城这种「两边各一个 is_main=1 主营地」的图，
        # 发错边就是「一进来在图外」（见 contracts.SPAWN_INDEX_BY_CAMP）。
        if wire.applyCampSpawn(camp):
            flow.result["logs"].append("spawn-side-chosen camp=" + str(camp)
                                       + " level=" + str(wire.LEVEL_ID)
                                       + " position=" + str(wire.POSITION))
        flow.send(0x23, struct.pack(">Hiiii", 2, 0, camp, 0, 0), "instance-camp-choose-result-zero")
    elif command == 0x23 and selector == 3:
        wire.exact(body, 3, "camp-choice-ok")
        camp = flow.session["camp"]
        flow.send(0x23, struct.pack(">Hi", 4, 0), "instance-camp-choose-ok-result-zero")
        # ⚠️ 2026-10-05：这两条**不带坐骑**。此刻玩家还没选将（选将是后面
        #    ``0x36/0x32`` 那条才到的），按 ini 默认将发马 ⇒ 他改选步兵之后，
        #    人身边那匹空马还留着（实机：赵云、黄忠各带一匹白马）。
        #    马改到 ``battleEntry()``（选将已定）那一份里发，一局只出现一次。
        flow.send(0xE, wire.actorVision(camp, heroIdOf(flow), withMount=False),
                  "instance-fixed-local-actor-vision-add-before-basic-info")
        sendCcObjects(flow)
        sendTutorialObjects(flow)
        sendNpcObjects(flow)
        flow.send(0xA, wire.actorInfo(flow.now, camp, withMount=False),
                  "instance-fixed-local-actor-after-camp-choice")
        flow.send(0x23, wire.campExchange(flow.now, camp), "instance-camp-exchange-notify")
        flow.session["actorImported"] = True
        flow.phase("actor-data-sent")
    elif command == 0xE and selector == 5:
        sequence = vision_flow.decode_vision_list_request(body)
        flow.send(0xE, vision_flow.encode_vision_list_response(
            sequence=sequence, object_mids=visionObjectMids(flow)),
            "instance-fixed-local-actor-vision-list")
        # 索引表**刚建好** —— 在这里补发一次 CC。
        # camp-choice-ok 那次送的会被客户端丢掉：那时它还没索引槽。
        # 实机取证：CC 报文比本响应早 50 条记录 / 7.9 秒。见 visionObjectMids。
        sendCcObjects(flow, force=True)
        sendTutorialObjects(flow, force=True)
        sendNpcObjects(flow, force=True)
    elif command == 0xE and selector == 7:
        request = vision_flow.decode_vision_get_objects_request(body)
        known = set(visionObjectMids(flow))
        if any(mid not in known for mid in request.object_mids):
            raise ValueError("VISION requested an object outside the fixed scene")
        for mid in request.object_mids:
            sendVisionObject(flow, mid)
        if request.object_mids:
            controls.activate(flow)
    elif command == 0x29 and selector == 0x65:
        container, sequence = login_flow.decode_item_container_request(b"\0\1" + body[2:])
        response = (wire.battleHeroes(sequence)
                    if container == 2 else b"\0\x66" + login_flow.encode_empty_item_container_response(
                        container, sequence)[2:])
        flow.send(0x29, response, "instance-sync-item-container")
    elif command == 0x36 and selector == 0x14:
        wire.exact(body, 3, "actor-load-ok")
        if body[2] != 0:
            raise ValueError("actor-load-ok requires result 0")
        if not flow.session.get("actorImported"):
            raise ValueError("actor-load-ok precedes fixed actor import")
        if not flow.session.get("loaded"):
            flow.session["loaded"] = True
            flow.send(0xA, wire.roundState(flow.now, 2, wire.PREPARE_MS), "instance-round-state-prepare")
            flow.later("actor-select", 1000)
            flow.later("round-start", wire.PREPARE_MS - wire.START_MS)
            flow.phase("prepare-sent")
    elif command == 0x36 and selector == 0x32:
        position = room_flow.decode_actor_choose_hero_request(body)
        if not 1 <= position <= len(wire.HERO_IDS):
            raise ValueError("fixed scene exposes hero slots 1..%d" % len(wire.HERO_IDS))
        if not flow.session.get("loaded"):
            raise ValueError("choose-hero precedes actor-load-ok")
        flow.session["heroChosen"] = True
        # ⭐ 2026-10-03：**玩家自己选的将**必须压过按关卡换将 ⇒ 打上 heroPicked 标记，
        #    ``heroIdOf()`` 见到它就原样返回 session 里的值，不再被 _ACTIVE_HERO 覆盖。
        flow.session["heroPicked"] = True
        flow.session["heroId"] = wire.HERO_IDS[position - 1]
        flow.send(0x36, room_flow.encode_actor_choose_hero_response(), "actor-choose-hero-result-zero")
        flow.send(0x36, room_flow.encode_actor_choose_hero_message(
            server_time_ms=wire.serverNowMs(), actor_mid=1, hero_resource_id=flow.session["heroId"],
            hero_badge=0), "actor-choose-hero-message")
        if flow.session.pop("prepareExpired", False):
            flow.later("round-start", 0)
    elif command == 0x36 and selector == 0x64:
        room_flow.decode_actor_play_request(body)
        if not flow.session.get("heroChosen"):
            raise ValueError("actor-play precedes hero selection")
        flow.send(0x36, room_flow.encode_actor_play_response(), "actor-play-result-zero")
        battleEntry(flow)
    else:
        return False
    return True
