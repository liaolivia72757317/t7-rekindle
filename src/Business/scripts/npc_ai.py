# -*- coding: utf-8 -*-
"""NPC 陪练 AI 控制器（门控：``T7_NPC_AI`` / ``[npc] ai``，默认关）。

为什么需要
----------
``npc.py`` 把训练 NPC 作为**静态** actor 视野对象下发（站着不动的木桩）；
``npc_battle.py`` 只处理「玩家砍 NPC」。本模块反过来：让 NPC **会朝玩家走位、
周期性挥砍、命中给玩家结算伤害**，变成一个能陪练的对手。

原理（全部复用既有已验证编码）
------------------------------
* **走位**：服务端定时 ticker 读玩家实时坐标（``controls.groundState(flow)``），
  算 NPC 下一步位置，发 ``cmd=2`` 的 actor 移动广播
  （照抄 ``controls.broadcast`` → ``encode_move_bc_with_system_and_active``，
  ``target_instance_id`` = NPC 自己的 instance_id）。
* **反伤**：进入近战距离后周期性发 ``battle_flow`` 的 ``battle_result`` /
  ``hit_point_notify``，把 ``attacker`` 设成 NPC、``target`` 设成玩家 ``ACTOR_ID``
  （镜像 ``npc_battle.py``，只是方向相反）。
* **带装备**：NPC 创建时镜像玩家当前手持武器（``wire.currentWeaponTid()``）。

⚠️ **首要实机未知数**：客户端是否接受「服务端驱动一个用本地 actor 编码器发出来的
NPC 移动/出招」？当年占位敌人（rid=2）也是这么发的、但从不广播，所以没验证过。
本模块**包在开关后，默认关**，绝不破坏现有静态靶行为；实测通过再长期开。

纪律（与 npc / ccobject / tutorial / npc_battle 一致）
---------------------------------------------------
开关关 / 场景没配 NPC / 任何异常 ⇒ 一个包都不发，战斗链逐位不变。
"""
import math
import os
import random

from . import battle, controls, npc, contracts as wire
from .codec import move_flow, battle_flow

# --- 可调参数（集中在这里，方便实机微调） ------------------------------------
TICK_MS = 250                 # AI 控制频率（每拍）
# NPC 移动速度（米/秒）。⚠️ 必须 ≈ 1.0：客户端靠 current_velocity 区分走/跑
# （controls.py 现场确认表：cv=1000=慢走，cv=5000=奔跑）。_sendMove 里 cv 取
# NPC_WALK_SPEED×1000，所以速度设多少、动画就按那个速度走，脚底下不滑步。
# 想让 NPC 走得快一点又不变成跑，只能微调到 ~1.3 以内（cv 越接近 1000 越稳）。
NPC_WALK_SPEED = 1.0
# ⚠️ 2026-10-04：用户「AI NPC 距离有点近，离远一点」⇒ 3.0 → 5.0。
# 这个值同时是「停步距离」和「绕玩家散开环的半径」。
# ⚠️ 必须与 ``npc_battle.MELEE_RANGE_M``（玩家砍 NPC 的命中距离）联动：
#   玩家命中距离必须 > 本值，否则 NPC 站在打不到的地方 ⇒ 打不死。
# 可用环境变量 ``T7_NPC_AI_RANGE`` / ``[npc] ai_range`` 现场微调（默认 5.0）。
NPC_MELEE_RANGE_M = 5.0       # 进入此距离即停步并开始攻击（默认 5.0 米）
NPC_ATTACK_COOLDOWN_MS = 1000  # 攻击间隔
NPC_DAMAGE_TO_PLAYER = 8.0     # 每击对玩家造成的伤害

NPC_ATTACK_ANIM_MS = 600        # 挥砍动画总时长（ms）：预备→挥砍→回待机
NPC_SLOT_SPREAD_DEG = 50.0      # 多个 NPC 绕玩家散开的角间距（避免叠一块）
# 攻击状态号取自 battle.py 近战链（ATTCK_INTENT_LEFT = (253,255,256,257)）：
#   253=预备 255=蓄力 256=过程(挥砍本身) 257=收招；2=ACT_STATE_IDLE 待机。
NPC_ATTACK_PREP_STATE = 253
NPC_ATTACK_SWING_STATE = 256
NPC_IDLE_STATE = 2

# 四个近战方向的（预备, 挥砍过程）状态号（battle.py 四向链首拍/第三拍）。
# 每次出手随机挑一个 ⇒ 两个 NPC 不再动作雷同、更自然。
_NPC_ATTACK_DIRS = (
    (235, 238),   # 上
    (241, 244),   # 右
    (247, 250),   # 下
    (253, 256),   # 左
)

NPC_AI_ENV = "T7_NPC_AI"
NPC_AI_INI_SECTION = "npc"
NPC_AI_INI_KEY = "ai"


def _knob():
    """**环境变量 > [npc] ai > 默认 off**。返回 bool。"""
    raw = os.environ.get(NPC_AI_ENV)
    if raw is None or not str(raw).strip():
        try:
            raw = wire.iniSection(NPC_AI_INI_SECTION, None).get(NPC_AI_INI_KEY, "")
        except Exception:  # noqa: BLE001
            raw = ""
    text = str(raw).strip().lower()
    if text in ("on", "all", "true", "yes", "1"):
        return True
    return False


ENABLED = _knob()


def _num(env, key, default):
    """**环境变量 > [npc] ini > 默认**，读一个浮点旋钮（读不到就返回默认）。

    与 ``_knob`` 同一套优先级；异常一律吞掉 ⇒ 启动行绝不能把服务端带崩。
    """
    raw = os.environ.get(env)
    if raw is None or not str(raw).strip():
        try:
            raw = wire.iniSection(NPC_AI_INI_SECTION, None).get(key, "")
        except Exception:  # noqa: BLE001
            raw = ""
    try:
        value = float(str(raw).strip())
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


# 停步/散开半径可现场微调（默认 5.0 米）。改 ini 后重启服务端生效。
NPC_MELEE_RANGE_M = _num("T7_NPC_AI_RANGE", "ai_range", NPC_MELEE_RANGE_M)


def _scene(flow):
    try:
        return controls.airWallScene(flow)
    except Exception:  # noqa: BLE001
        return flow.session.get("scene") or ""


def start(flow):
    """发完 NPC 后调用：初始化 AI 状态并起 ticker。幂等（已在跑则不再叠加）。"""
    if not ENABLED:
        return
    try:
        scene = _scene(flow)
        actors = npc.actors(scene)
        if not actors:
            return
        table = flow.session.setdefault("npcAi", {})
        now0 = wire.serverNowMs()
        for a in actors:
            key = str(a["mid"])
            cd_mul = round(random.uniform(0.8, 1.3), 2)
            table.setdefault(key, {
                "pos": list(a["pos"]),
                "instance_id": a["instance_id"],
                "mid": a["mid"],
                "animState": -1,        # 上次下发的 state-sync 状态（-1=未发）
                "attackAnimUntil": 0,   # 本次挥砍动画到期时间(ms)
                "dealt": True,          # 本次挥砍是否已结算伤害
                "seq": 0,               # state-sync 序号（每 NPC 自增）
                # —— 个体差异化：避免两个 NPC 动作雷同/锁步 ——
                "cdMul": cd_mul,                                     # 攻击间隔倍率
                "speedMul": round(random.uniform(0.85, 1.15), 2),   # 移速倍率
                "animMsMul": round(random.uniform(0.8, 1.2), 2),    # 挥砍时长倍率
                "lastAttack": now0 - int(random.uniform(            # 初始相位错开
                    0, NPC_ATTACK_COOLDOWN_MS * cd_mul)),
            })
        if not flow.session.get("npcAiRunning"):
            flow.session["npcAiRunning"] = True
            flow.later("npc-ai-tick", TICK_MS)
    except Exception as error:  # noqa: BLE001
        try:
            flow.result["logs"].append("npc-ai-start-failed " + repr(error))
        except Exception:  # noqa: BLE001
            pass


def timer(flow, name):
    """handleTimer 的 dispatch 钩子；只认 ``npc-ai-tick``，其余交给链上其他模块。"""
    if name != "npc-ai-tick":
        return False
    try:
        _tick(flow)
    except Exception as error:  # noqa: BLE001
        try:
            flow.result["logs"].append("npc-ai-tick-failed " + repr(error))
        except Exception:  # noqa: BLE001
            pass
    return True


def _valid(flow):
    if not ENABLED:
        return False
    if flow.session.get("role") != "instance" or flow.session.get("leaving"):
        return False
    if not flow.session.get("battleEntered"):
        return False
    return True


def _tick(flow):
    if not _valid(flow):
        flow.session["npcAiRunning"] = False
        return
    scene = _scene(flow)
    table = flow.session.get("npcAi")
    if not table:
        flow.session["npcAiRunning"] = False
        return

    # --- 玩家实时坐标 / 朝向 ---
    try:
        ground = controls.groundState(flow)
        ppos = ground.get("position") or wire.POSITION
        pheading = ground.get("heading", 0) or 0
    except Exception:  # noqa: BLE001
        ppos = wire.POSITION
        pheading = 0
    if not ppos:
        flow.later("npc-ai-tick", TICK_MS)
        return

    dead = flow.session.get("npcDead") or {}
    now = wire.serverNowMs()
    # 只处理存活 NPC，并记下总数用于散开槽位
    alive = [(k, st) for k, st in table.items() if not dead.get(k)]
    n = len(alive)
    pth = math.radians(pheading)
    fwd_x, fwd_y = math.cos(pth), -math.sin(pth)   # 玩家世界前向
    for idx, (key, st) in enumerate(alive):
        npc_pos = st["pos"]
        dx = ppos[0] - npc_pos[0]
        dy = ppos[1] - npc_pos[1]
        dist = math.hypot(dx, dy)
        # 朝向玩家：世界前向 (cosθ, -sinθ) 对齐 (dx,dy) ⇒ θ = atan2(-dy, dx)
        heading = math.degrees(math.atan2(-dy, dx)) if dist > 1e-6 else pheading
        speed = NPC_WALK_SPEED * st.get("speedMul", 1.0)
        if dist > NPC_MELEE_RANGE_M:
            # 走向玩家前方环形上的「专属槽位」—— 绕玩家散开，避免叠一块。
            # 槽位角 = 玩家前向偏转 (idx-(n-1)/2)*间距；目标点 = 玩家 + 前向*R 再旋转。
            # ⚠️ 间距自适应（2026-10-04）：NPC 多时自动收窄，保证绕玩家一圈不溢出
            #    360°（10 个时取 360/10=36°，9×36=324° < 360，不会首尾重叠）。
            spread = min(NPC_SLOT_SPREAD_DEG, 360.0 / max(n, 1))
            ang = math.radians((idx - (n - 1) / 2) * spread)
            ca, sa = math.cos(ang), math.sin(ang)
            dir_x = fwd_x * ca - fwd_y * sa
            dir_y = fwd_x * sa + fwd_y * ca
            tp = (ppos[0] + dir_x * NPC_MELEE_RANGE_M,
                  ppos[1] + dir_y * NPC_MELEE_RANGE_M, ppos[2])
            mdx, mdy = tp[0] - npc_pos[0], tp[1] - npc_pos[1]
            mdist = math.hypot(mdx, mdy)
            if mdist > 1e-6:
                step = min(speed * TICK_MS / 1000.0, mdist)
                new_pos = [npc_pos[0] + mdx / mdist * step,
                           npc_pos[1] + mdy / mdist * step, npc_pos[2]]
            else:
                new_pos = list(npc_pos)
            st["pos"] = new_pos
            _sendMove(flow, st["instance_id"], new_pos, heading, moving=True, speed=speed)
            # 走路时由移动广播(cv)驱动步行动画，不发 state-sync；重置动画状态
            st["animState"] = -1
            continue
        # ---- 近身：停步 + 挥砍动画 + 反伤 ----
        st["pos"] = list(npc_pos)
        _sendMove(flow, st["instance_id"], npc_pos, heading, moving=False)
        cd = NPC_ATTACK_COOLDOWN_MS * st.get("cdMul", 1.0)
        last = st.get("lastAttack", -10 ** 9)
        if now - last >= cd:
            st["lastAttack"] = now
            prep, swing = random.choice(_NPC_ATTACK_DIRS)   # 每次出手随机方向
            st["prepState"] = prep
            st["swingState"] = swing
            st["animDur"] = int(NPC_ATTACK_ANIM_MS * st.get("animMsMul", 1.0))
            st["attackAnimUntil"] = now + st["animDur"]
            st["dealt"] = False
        # 动画状态机：预备 → 挥砍(出手) → 待机
        if now < st.get("attackAnimUntil", 0):
            if now < st["attackAnimUntil"] - st["animDur"] // 2:
                desired = st.get("prepState", NPC_ATTACK_PREP_STATE)
            else:
                desired = st.get("swingState", NPC_ATTACK_SWING_STATE)
                if not st.get("dealt"):       # 出手瞬间结算伤害
                    st["dealt"] = True
                    _attackPlayer(flow, st["mid"], npc_pos)
        else:
            desired = NPC_IDLE_STATE
        if desired != st.get("animState"):
            _sendStateSync(flow, st["instance_id"], st, desired)
            st["animState"] = desired
    flow.later("npc-ai-tick", TICK_MS)


def _sendMove(flow, instance_id, position, heading, moving, speed=NPC_WALK_SPEED):
    """发一条 NPC 移动广播（镜像 player 的 broadcast，但下行显示轴直接给正值）。"""
    try:
        tick = flow.session.get("npcAiTick", 0) + 1
        flow.session["npcAiTick"] = tick
        if moving:
            state = 2                      # 前进（世界 FB=-1 ⇒ 显示 FB=+1000）
            fb, lr = 1000, 0
            # ⚠️ cv 决定动画：1000=慢走，5000=奔跑（controls.py 现场确认表）。
            # 取「慢走」并让 cv 与该 NPC 真实移速严格相等 ⇒ 脚底下不滑步。
            cv = int(round(speed * 1000))
            mv = max(cv, 1000)             # 步行上限=步行速度
        else:
            state = move_flow.MOVE_GROUND_STATE_STOP
            fb, lr = 0, 0
            cv, mv = 0, 0
        yaw = int(round(heading))
        if yaw > 180:
            yaw -= 360
        elif yaw < -180:
            yaw += 360
        body = move_flow.encode_move_bc_with_system_and_active(
            server_tick=tick & 0xFFFFFFFF,
            target_instance_id=instance_id,
            position=tuple(position),
            direction_yaw=yaw,
            state=state,
            left_right=lr,
            forward_back=fb,
            current_velocity=cv,
            max_velocity=mv,
            system_group=3,
            active=1,
            acceleration=0)
        flow.send(2, body, "npc-ai-move inst=%d mv=%d" % (instance_id, 1 if moving else 0))
    except Exception as error:  # noqa: BLE001
        try:
            flow.result["logs"].append("npc-ai-move-failed " + repr(error))
        except Exception:  # noqa: BLE001
            pass


def _attackPlayer(flow, npc_mid, npc_pos):
    """NPC 砍玩家一刀：权威伤害 + 命中特效（镜像 npc_battle，方向相反）。

    ⚠️ 格挡判定（2026-10-04）：读玩家当前动作意图 ``battle.actionState(flow)["intent"]``，
    若是四向招架意图（300580/300590/300600/300610）则伤害归零 ⇒ 玩家格挡**有效**
    （「格档有用」）。否则按 ``NPC_DAMAGE_TO_PLAYER`` 正常结算。
    """
    try:
        now = wire.serverNowMs()
        seq = int(flow.session.get("action", {}).get("seq", 1))
        intent = battle.actionState(flow).get("intent", 0)
        blocked = intent in (battle.BLOCK_INTENT_UP, battle.BLOCK_INTENT_RIGHT,
                             battle.BLOCK_INTENT_DOWN, battle.BLOCK_INTENT_LEFT)
        dmg = 0 if blocked else int(NPC_DAMAGE_TO_PLAYER)
        flow.send(
            battle_flow.BATTLE_COMMAND,
            battle_flow.encode_battle_result(
                attacker_rid=npc_mid,
                target_rid=wire.ACTOR_ID,
                attacker_seq_no=seq,
                target_life_state=battle_flow.LIFE_STATE_NONE,
                attack_weapon_id=0,
                target_result_type=battle_flow.RESULT_DATA_NORMAL_DAMAGE,
                target_result_data=dmg),
            "npc-ai-attack target=player dmg=%d blocked=%s from=%d"
            % (dmg, "yes" if blocked else "no", npc_mid))
        hitPos = (npc_pos[0], npc_pos[1], npc_pos[2] + 1.0)
        flow.send(
            battle_flow.BATTLE_COMMAND,
            battle_flow.encode_battle_hit_point_notify(
                hit_pos=hitPos, attacker_mid=npc_mid, target_mid=wire.ACTOR_ID,
                attacker_weapon_id=0, target_weapon_id=0, server_time_ms=now),
            "npc-ai-hit-fx target=player")
    except Exception as error:  # noqa: BLE001
        try:
            flow.result["logs"].append("npc-ai-attack-failed " + repr(error))
        except Exception:  # noqa: BLE001
            pass


def _sendStateSync(flow, instance_id, st, state):
    """下发一次 STATE_SYNC_SIMPLE（cmd=4/sel=3）让 NPC 播动作。

    与 ``battle.syncState`` 同一编码器（``battle_flow.encode_battle_state_sync_simple``），
    只是 ``instance_id`` 换成 NPC 自己的，而不是写死玩家 ``ACTOR_ID``（投石车 siege.py
    也是这么驱动 SC 物件 actor 的）。``state`` 取近战链状态号：253=预备 / 256=挥砍过程 / 2=待机。
    """
    try:
        st["seq"] = (st.get("seq", 0) % 0xFFFF) + 1
        body = battle_flow.encode_battle_state_sync_simple(
            instance_id=instance_id, seq_no=st["seq"], state=state,
            state_change_ms=0, state_time_ms=0)
        flow.send(battle_flow.BATTLE_COMMAND, body,
                  "npc-ai-state inst=%d state=%d" % (instance_id, state))
    except Exception as error:  # noqa: BLE001
        try:
            flow.result["logs"].append("npc-ai-state-failed " + repr(error))
        except Exception:  # noqa: BLE001
            pass


def describe():
    try:
        return ("ai=%s speed=%.1fm range=%.1fm cd=%dms dmg=%.0f"
                % ("on" if ENABLED else "off", NPC_WALK_SPEED, NPC_MELEE_RANGE_M,
                   NPC_ATTACK_COOLDOWN_MS, NPC_DAMAGE_TO_PLAYER))
    except Exception:  # noqa: BLE001
        return "report-failed"


# 启动自证行（与 [npc] / [cc] / [guide] 同一风格）。
print("[npc-ai] " + describe(), flush=True)
