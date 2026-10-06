"""``CS_PROTO_MO_PKG``（command = ``SH_CS_CMD_MO`` = 18，地图物件）的编码器。

出处（全部来自 ``TieJiClient.exe`` / metalib ``sh_proto_cs``，2026-09-20 反解）
--------------------------------------------------------------------------
::

    CS_PROTO_MO_PKG
      @0  smallint msg_id    消息ID（就是服务端日志里的 ``selector``）
      @2  CS_PROTO_MO_DATA   union，按 msg_id 选分支

``CS_PROTO_MO_DATA`` 分支（storage_size 一并列出，供校验）：

======  =======================================  ======  ==========
msg_id  名称                                      尺寸     方向
======  =======================================  ======  ==========
1       CS_PROTO_MO_INTERACT                     17      C->S
2       CS_PROTO_MO_CANCEL_INTERACT              8       C->S
3       CS_PROTO_MO_UPDATE_STATE                 100     S->C
4       CS_PROTO_MO_CONTROL_ON                   16      S->C  开始控制
5       CS_PROTO_MO_CONTROL_OFF                  16      S->C  取消控制
6       CS_PROTO_MO_SHOOT_ROCK                   40      S->C  发射弹药
13      CS_PROTO_MO_HAVOK_RES_INDEX_NTF          9       S->C  Havok资源索引
16      CS_PROTO_MO_INCLINE_NTF                  16      S->C  MO倾斜度
12      CS_PROTO_MO_PULL_STATE_REQ               1       C->S
21      CS_PROTO_MO_START_INTERACT_NTF           16      S->C
22      CS_PROTO_MO_STOP_INTERACT_NTF            17      S->C
23      CS_PROTO_MO_INTERACT_RSP                 20      S->C
27      CS_PROTO_MO_ACTOR_CONTROL_NTF            16      S->C  角色控制MO
======  =======================================  ======  ==========

⚠️ 上表只是本模块**已实现**的分支。``CS_PROTO_MO_DATA`` union 全表共 **27 条**
（2026-09-23 用 ``python -m codec union --union CS_PROTO_MO_DATA`` 全量解出），
未实现的还有 7/8/9/10/11/14/15/18/19/20/24/25/26/28。
要加新消息：先跑那条 union 拿 sel/尺寸，再 ``-m codec struct`` 拿字段，别凭印象。

⚠️ 未验证边界
--------------
* ``update_mo_state`` 里 ``event_var`` 按 ``CS_PROTO_ANIMATION_EVENT_AND_VAR``
  的 **count 引用**规则编码，与 ``vision_flow.encode_cc_dynamic_info`` 里已实机
  验证过的写法一致。**唯一被验证过的形式是 ``event_num=0, param_num=0``**（2 字节）；
  非零 event / 非零 param 是否被客户端采信 **未实测**（2026-09-27 起
  ``born`` / ``crop`` 两条通道就是在测这件事）。
* ``state_data`` 语义未知，一律发 0。
"""
import struct

# --- 消息ID（= 服务端日志里的 selector） --------------------------------------
MO_INTERACT = 1             # C->S 地图物件交互请求
MO_CANCEL_INTERACT = 2      # C->S 取消交互
MO_UPDATE_STATE = 3         # S->C 更新地图物件状态
MO_CONTROL_ON = 4           # S->C 开始控制          ← 2026-09-23 补齐
MO_CONTROL_OFF = 5          # S->C 取消控制          ← 2026-09-23 补齐
MO_SHOOT_ROCK = 6           # S->C 发射弹药（投石车） ← 2026-09-23 补齐
MO_ROCK_HIT = 7             # C->S 弹药命中           ← 2026-09-29 补齐（投石真的飞了的证据）
MO_RB_TRANSFORM = 8         # S->C 刚体的变换通知     ← 2026-09-24 起用
MO_PULL_STATE_REQ = 12      # C->S 查询所有 MO 状态
MO_HAVOK_RES_INDEX_NTF = 13 # S->C MO 的 Havok 资源索引通知 ← 2026-09-23 补齐
MO_INCLINE_NTF = 16         # S->C MO 倾斜度通知      ← 2026-09-23 补齐（云梯"立起来"）
MO_START_INTERACT_NTF = 21  # S->C 开始交互通知
MO_STOP_INTERACT_NTF = 22   # S->C 停止交互通知
MO_INTERACT_RSP = 23        # S->C 交互请求结果
MO_ACTOR_CONTROL_NTF = 27   # S->C 角色控制 MO 通知   ← 2026-09-23 补齐

MO_COMMAND = 18             # SH_CS_CMD_MO

# --- ``CS_PROTO_MO_DATA`` 全表 28 条（selector → 结构名），2026-09-29 TDR 直读 ------
#
# 出处：``python -m codec union --union CS_PROTO_MO_DATA --metalib sh_proto_cs``。
# **sel=17 是空缺**（16 直接跳到 18），不是笔误 —— 表里就没有这一条。
#
#   sel 结构名                       方向  说明
#    1  MO_INTERACT                 C->S  地图物件交互请求
#    2  MO_CANCEL_INTERACT          C->S  取消交互
#    3  MO_UPDATE_STATE             S->C  更新地图物件状态
#    4  MO_CONTROL_ON               S->C  开始控制
#    5  MO_CONTROL_OFF              S->C  取消控制
#    6  MO_SHOOT_ROCK               S->C  发射弹药
#    7  MO_ROCK_HIT                 C->S  弹药命中          ← 客户端回报「我砸到了谁」
#    8  MO_RB_TRANSFORM             S->C  刚体变换通知
#    9  MO_CHOOSE_BUILDING_REQ      C->S  选择建造物请求
#   10  MO_CHOOSE_BUILDING_RSP      S->C  选择建造物回复
#   11  MO_BUILD_RESULT             S->C  建造结果
#   12  MO_PULL_STATE_REQ           C->S  查询所有 MO 状态
#   13  MO_HAVOK_RES_INDEX_NTF      S->C  MO 的 Havok 资源索引通知
#   14  MO_BUILDING_INFO_NTF        S->C  拥有的建造物信息通知
#   15  MO_BUILDING_COMPLETENESS_NTF S->C 建造物完成度通知
#   16  MO_INCLINE_NTF              S->C  MO 倾斜度通知（云梯立起）
#   18  MO_DROP_PICK_MO_REQ         C->S  放下 MO 请求
#   19  MO_DROP_PICK_MO_RSP         S->C  放下 MO 回复
#   20  MO_PICKING_INFO_NTF         S->C  MO 携带信息通知
#   21  MO_START_INTERACT_NTF       S->C  开始交互通知
#   22  MO_STOP_INTERACT_NTF        S->C  停止交互通知
#   23  MO_INTERACT_RSP             S->C  交互请求结果
#   24  MO_BALLISTA_ROTATE_REQ      C->S  弩机旋转请求
#   25  MO_BALLISTA_ROTATE_NTF      S->C  弩机旋转通知
#   26  MO_BALLISTA_BULLET_NTF      S->C  弩机弹药通知
#   27  MO_ACTOR_CONTROL_NTF        S->C  角色控制 MO 通知
#   28  MO_SIEGE_BUILDING_NUM_NTF   S->C  本方攻城器械数量通知
#
# ⭐⭐ **全表里「射出」方向只有 sel=6 一条（S->C），没有任何 C->S 的"请求射出"**。
#     ⇒ 投石车的发射**只能由服务端发起**，客户端无法请求。
#     ⚠️ 与之对照：sel=25 ``MO_BALLISTA_ROTATE_NTF`` 看着像 A/D 转向的答案，
#     但**本客户端 build 里没有实现**（exe 里 ``Ballista`` 出现 0 次，
#     70 个 ``GeRecv*`` 接收器里没有任何 rotate 接收器）⇒ 发了也不会有反应。

# --- 错误码 ------------------------------------------------------------------
SH_ERR_SUCCESS = 0          # SH_ERROR_DEF: 成功


def _pkg(msg_id: int, payload: bytes) -> bytes:
    """``CS_PROTO_MO_PKG``：2 字节 msg_id + 分支载荷。"""
    return struct.pack(">H", msg_id) + payload


def encode_interact_rsp(target: int, cli_tick: int, result: int = SH_ERR_SUCCESS) -> bytes:
    """``CS_PROTO_MO_INTERACT_RSP``（msg_id=23）。

    ``@0 u64 target / @8 u64 cli_tick / @16 i32 result``
    """
    return _pkg(MO_INTERACT_RSP, struct.pack(">QQi", target & 0xFFFFFFFFFFFFFFFF,
                                             cli_tick & 0xFFFFFFFFFFFFFFFF, result))


def encode_update_state(target: int, state: int, state_change_ms: int,
                        state_time_ms: int, state_data: int = 0,
                        events=(), params=()) -> bytes:
    """``CS_PROTO_MO_UPDATE_STATE``（msg_id=3）—— **状态回流**，客户端据此播动画。

    ``@0 u64 target / @8 i32 state / @12 u64 state_data /
     @20 u64 state_change_ms / @28 i32 state_time_ms / @32 event_var``

    ``events`` = ``E_MO_EVENT_*`` 值序列（最多 3 个），``params`` = ``(param_id, 浮点值)``
    序列（最多 10 个）。**两个都留空时与改动前逐字节相同**（仍是 ``0,0`` 两字节）。
    """
    return _pkg(MO_UPDATE_STATE,
                struct.pack(">QiQQi", target & 0xFFFFFFFFFFFFFFFF, state,
                            state_data & 0xFFFFFFFFFFFFFFFF,
                            state_change_ms & 0xFFFFFFFFFFFFFFFF, state_time_ms)
                + encode_animation_event_var(events, params))


# --- 2026-09-23 补齐：MO 全表里与本轮三件事直接相关的分支 ----------------------
#
# 出处：``TieJiClient.exe`` / metalib ``sh_proto_cs``，
#       ``python -m codec union --union CS_PROTO_MO_DATA``（全表 27 条）+
#       ``-m codec struct <名>``（逐条字段）。字节布局**全部直读 TDR，不是推断**。
#
# ``E_MO_EVENT_*``（同 metalib，2026-09-22 已收进
# ``hkx_decode/out/mo_state_macros_full.md``）：event_var 里带的就是这一族。
MO_EVENT_BE_OP = 1001                # 被操作 —— 对应行为树 投石车_被操控待机
MO_EVENT_ACTOR_ENTER_REGION = 1002   # 角色进入区域
MO_EVENT_ACTOR_LEAVE_REGION = 1003   # 角色离开区域
MO_EVENT_ADVANCE_MOVE_COMPLETE = 1010  # 前进移动完成
MO_EVENT_BACK_MOVE_COMPLETE = 1011     # 后退移动完成
MO_EVENT_START_CONTROL = 1012        # 开始操控 —— 对应行为树 移动_投石车
MO_EVENT_STOP_CONTROL = 1013         # 停止操控
MO_EVENT_REACH_PATH_POINT = 1030     # 到达路径点（攻城车自动前进的每站）

# event_var 的容量上限（``CS_PROTO_ANIMATION_EVENT_AND_VAR`` 的数组维度）
_EVENT_SLOTS = 3
_PARAM_SLOTS = 10


def encode_animation_event_var(events=(), params=()) -> bytes:
    """``CS_PROTO_ANIMATION_EVENT_AND_VAR``（动画系统事件和参数）。

    TDR 布局（``-m codec struct CS_PROTO_ANIMATION_EVENT_AND_VAR``）::

        @0  event_num  char          事件数目
        @1  event      short  count=3 事件类型（E_MO_EVENT_*）
        @7  param_num  char          参数数目
        @8  param      CS_PROTO_ANIMATION_PARAM count=10

    ⚠️ 数组是**定容 3 / 10**，线上只写 ``count`` 个元素（count 引用规则）——
    与 ``vision_flow`` 里已实机验证过的写法一致。所以 ``events=()`` 得到 2 字节。

    ``params`` = ``(param_id, 浮点值)`` 序列，最多 10 条。``param_id`` 取值见
    ``E_SH_HAVOK_HARDCODE_PARAM_DEF``（客户端 metalib ``sh_proto_cs``，2026-09-27
    用 ``tdr/dump_group.py`` 直读）；本模块**不认识任何 id**，只负责编码 ——
    语义（哪个 id 对应哪条 Havok 变量）由调用方 ``siege`` 负责，见
    ``siege.HAVOK_PARAM_TYPE_CROPTIME``。

    ``CS_PROTO_ANIMATION_PARAM`` 的线上尺寸是 **6 字节**（TDR 直读，
    ``storageSize=6``，无填充）：``param_id`` short(2) + ``param_value`` float(4)。
    ⇒ 本函数返回长度 = ``1 + 2*len(events) + 1 + 6*len(params)``。

    ⚠️ **未实测**：``events`` 里那个 short 的端序。唯一被实机验证过的形式是
    ``event_num=0, param_num=0``（= 2 字节，``vision_flow`` 那条），
    非零事件**从未确认送达过**。整包其余字段全部大端（已由状态能生效反证），
    所以这里沿用 ``>h`` / ``>f``；若 ``born`` 通道实机「按 C 毫无反应」，
    第一个要怀疑的就是这里。
    """
    events = tuple(events)
    params = tuple(params)
    if len(events) > _EVENT_SLOTS:
        raise ValueError("animation event_var allows at most %d events" % _EVENT_SLOTS)
    if len(params) > _PARAM_SLOTS:
        raise ValueError("animation event_var allows at most %d params" % _PARAM_SLOTS)
    body = struct.pack(">b", len(events))
    for value in events:
        body += struct.pack(">h", int(value))
    body += struct.pack(">b", len(params))
    for param_id, param_value in params:
        # CS_PROTO_ANIMATION_PARAM：param_id short + param_value float，共 6 字节
        body += struct.pack(">hf", int(param_id), float(param_value))
    return body


def encode_control_on(actor_mid: int, mo_mid: int) -> bytes:
    """``CS_PROTO_MO_CONTROL_ON``（msg_id=4，16B）—— S->C **开始控制**。

    ``@0 actor_mid u64 角色 / @8 mo_mid u64 被控制物件``

    客户端收到后走 ``AVGeRecvSiegeControlOn``（见 exe 字符串），把**玩家**切到
    「操控攻城器械」状态族（``propsheet/battlestate/攻城器械类.psheet``，值全是
    ``武将_弩机_*``；投石车走 ``投石车.psheet`` 的 ``武将_投石车_*``），
    并允许 A/D 转向、蓄力（``AVGeSetCatapultChargeTimerAction``）。

    ⚠️ 这就是「按 C 人物和车绑定、可操控投石车」缺的那条报文。
    """
    return _pkg(MO_CONTROL_ON, struct.pack(">QQ",
                                           actor_mid & 0xFFFFFFFFFFFFFFFF,
                                           mo_mid & 0xFFFFFFFFFFFFFFFF))


def encode_control_off(actor_mid: int, mo_mid: int) -> bytes:
    """``CS_PROTO_MO_CONTROL_OFF``（msg_id=5，16B）—— S->C 取消控制。

    字段与 ``CONTROL_ON`` 相同。松手/走远时必须发，否则玩家卡在器械上
    （对应行为树 ``移动_投石车`` 的 ``LEAVE_DEMOLISHER`` 分支）。
    """
    return _pkg(MO_CONTROL_OFF, struct.pack(">QQ",
                                            actor_mid & 0xFFFFFFFFFFFFFFFF,
                                            mo_mid & 0xFFFFFFFFFFFFFFFF))


def encode_actor_control_ntf(actor_mid: int, mo_mid: int) -> bytes:
    """``CS_PROTO_MO_ACTOR_CONTROL_NTF``（msg_id=27，16B）—— 角色控制 MO 通知。

    ``@0 actor_mid u64 控制角色 / @8 mo_mid u64 MO``。语义与 CONTROL_ON 的区别
    未实测（名字是「通知」不是「开/关」），**默认与 CONTROL_ON 前后脚一起发**，
    可用 ``[cc] cat_actor_ntf=off`` 单独关掉做 A/B。
    """
    return _pkg(MO_ACTOR_CONTROL_NTF, struct.pack(">QQ",
                                                  actor_mid & 0xFFFFFFFFFFFFFFFF,
                                                  mo_mid & 0xFFFFFFFFFFFFFFFF))


def encode_incline_ntf(server_time_ms: int, mo_mid: int, angle_radians: float) -> bytes:
    """``CS_PROTO_MO_INCLINE_NTF``（msg_id=16，16B）—— S->C **MO 倾斜度通知**。

    ``@0 svr_time u32 服务器时间(毫秒) / @4 mo_mid u64 建造物MO /
      @12 incline_angle float MO倾斜**弧度**``

    ⭐ 这就是「云梯立起来」那条报文。依据链：
      ① 用户实机：按 C 后梯子**横躺**不动（截图可证），从未立起；
      ② 行为树 ``云梯_出生.btree`` 只做「设置 Character=TSZZ_stair_04.hkt + 动画事件」，
         **没有任何"旋转"动作** ⇒ 姿态只能由外部驱动；
      ③ 客户端配置表 ``propsheet/攻城器械.psheet`` 里云梯（``器械类型=1``，
         记录名为空串）的 ``nav文件 = TSZZ_stair_04.hkt``（与行为树同一字符串），
         且 ``触发参数2 = 1.57``（= π/2 = 90°，**就是立直角**）；
      ④ 协议表里存在一条**专门给 MO 传倾角**的消息（本条），而我们从未发过。
    ⇒ 立梯 = 把 ``incline_angle`` 从 0 推到 1.57。客户端**自己插值**，
      服务端只需按节奏给中间值（见 ``siege.LADDER_TILT_STEPS``）。
    """
    return _pkg(MO_INCLINE_NTF, struct.pack(">IQf",
                                            server_time_ms & 0xFFFFFFFF,
                                            mo_mid & 0xFFFFFFFFFFFFFFFF,
                                            float(angle_radians)))


# ``CS_PROTO_MO_RB_TRANSFORM.trans_array`` 的数组维度（TDR count=5）
_POSES_SLOTS = 5


def encode_rb_transform(target: int, time_stamp: int, poses) -> bytes:
    """``CS_PROTO_MO_RB_TRANSFORM``（msg_id=8）—— S->C **刚体的变换通知**。

    ⭐ 2026-09-24 新解出。与 ``encode_incline_ntf`` 是**两条不同的通道**：

    * ``INCLINE_NTF`` 只给**一个标量角度** ⇒ 客户端自己决定绕哪转。
      实测它绕**模型原点**转，而被客户端标注为「梯脚」的那端在局部 X = −8.038
      （``攻城器械.psheet`` ``[触发区域]LocalPosX = −8`` 独立印证），
      于是梯脚沉到地下 8 m、顶端只到半根高 —— 就是「搭得低了，再高一半」。
    * ``RB_TRANSFORM`` 直接给**位置 + 旋转** ⇒ 绕哪转、转到哪，由我们说了算。

    TDR 直读（``-m codec struct CS_PROTO_MO_RB_TRANSFORM``，metalib
    ``sh_proto_cs``，源 TieJiClient.exe）::

        @0  target       biguint   u64   MO 的 mid
        @8  time_stamp   uint64         服务器时间(ms)
        @16 array_count  tinyuint  u8   （count 引用）
        @17 trans_array  CS_PROTO_HAVOK_POS_AND_ROT  count=5，每条 28B

    ``CS_PROTO_HAVOK_POS_AND_ROT``（同法直读，``desc='位置和旋转'``）::

        @0  pos_x / pos_y / pos_z           float  位置
        @12 rot_0 / rot_1 / rot_2 / rot_3   float  旋转（4 分量）

    ``poses`` = ``((pos, rot), ...)``：``pos`` 3 元组、``rot`` 4 元组，最多 5 条。
    **空序列 ⇒ 整条只有 17 + 2 字节**（不写 trans_array），与「不做这件事」等价。

    ⚠️ 未实测：``rot_0..3`` 的分量顺序（``wxyz`` 还是 ``xyzw``）。由
    ``siege.ladderRbQuatOrder()`` 旋钮决定，默认 ``wxyz``；
    这是本函数唯一的假设，其余字节全部直读 TDR。
    """
    poses = tuple(poses)
    if len(poses) > _POSES_SLOTS:
        raise ValueError("rb_transform allows at most %d poses" % _POSES_SLOTS)
    body = struct.pack(">QQB", target & 0xFFFFFFFFFFFFFFFF,
                       time_stamp & 0xFFFFFFFFFFFFFFFF, len(poses))
    for pose in poses:
        pos, rot = pose
        px, py, pz = (float(v) for v in pos)
        r0, r1, r2, r3 = (float(v) for v in rot)
        # 7 个 float = 28 字节，与 CS_PROTO_HAVOK_POS_AND_ROT.storageSize 一致
        body += struct.pack(">7f", px, py, pz, r0, r1, r2, r3)
    return _pkg(MO_RB_TRANSFORM, body)


def encode_havok_res_index_ntf(mo_mid: int, havok_res_index: int) -> bytes:
    """``CS_PROTO_MO_HAVOK_RES_INDEX_NTF``（msg_id=13，9B）—— MO 的 Havok 资源索引。

    ``@0 mo_mid u64 / @8 havok_res_index int8``

    ⚠️ 与本模块 & ``ccobject`` 里那个「CC 动态物件报文自带的 ``havok_res_index``
    字段」是两个不同通道。TDR 直读：CC 报文里那个字段确实存在，而这条是**单独
    一条通知**。二者谁是客户端最终采信的、生效顺序如何，**未实测** ⇒ 做成独立开关
    （``[cc] cc_havok_ntf``），别和 CC 报文里那个旋钮混在一起 A/B。
    """
    if not -128 <= havok_res_index <= 127:
        raise ValueError("havok_res_index must fit int8")
    return _pkg(MO_HAVOK_RES_INDEX_NTF,
                struct.pack(">Qb", mo_mid & 0xFFFFFFFFFFFFFFFF, int(havok_res_index)))


def encode_shoot_rock(attacker: int, flyer: int,
                      position, velocity) -> bytes:
    """``CS_PROTO_MO_SHOOT_ROCK``（msg_id=6，40B）—— S->C 发射弹药（投石车）。

    ``@0 attacker u64 攻击者 / @8 flyer u64 飞行物 / @16 pos 3×float / @28 velocity 3×float``

    调用方：``siege``。⚠️ 需要服务端自己在天上积分弹道（客户端只收到初速初位），
    所以默认**不自动发射**，只在 ``[cc] cat_shoot=on`` 时才用。
    """
    px, py, pz = (float(v) for v in position)
    vx, vy, vz = (float(v) for v in velocity)
    return _pkg(MO_SHOOT_ROCK,
                struct.pack(">QQffffff", attacker & 0xFFFFFFFFFFFFFFFF,
                            flyer & 0xFFFFFFFFFFFFFFFF,
                            px, py, pz, vx, vy, vz))


def encode_start_interact_ntf(mo_mid: int, actor_mid: int) -> bytes:
    """``CS_PROTO_MO_START_INTERACT_NTF``（msg_id=21）。"""
    return _pkg(MO_START_INTERACT_NTF, struct.pack(">QQ", mo_mid & 0xFFFFFFFFFFFFFFFF,
                                                   actor_mid & 0xFFFFFFFFFFFFFFFF))


def encode_stop_interact_ntf(mo_mid: int, actor_mid: int, is_interrupt: int = 0) -> bytes:
    """``CS_PROTO_MO_STOP_INTERACT_NTF``（msg_id=22）。"""
    return _pkg(MO_STOP_INTERACT_NTF,
                struct.pack(">QQb", mo_mid & 0xFFFFFFFFFFFFFFFF,
                            actor_mid & 0xFFFFFFFFFFFFFFFF, is_interrupt))


def decode_interact(body: bytes):
    """``CS_PROTO_MO_INTERACT``（msg_id=1）→ ``(target, cli_tick, type)``。

    ``@0 u64 target 器械rid / @8 u64 cli_tick 客户端时间 /
     @16 int8 type 交互类型（E_INTERACT_TYPE_DEF）``
    """
    if len(body) < 19:
        raise ValueError("CS_PROTO_MO_INTERACT requires 19 bytes, got %d" % len(body))
    target, cli_tick = struct.unpack_from(">QQ", body, 2)
    return target, cli_tick, struct.unpack_from(">b", body, 18)[0]


def decode_cancel_interact(body: bytes) -> int:
    """``CS_PROTO_MO_CANCEL_INTERACT``（msg_id=2）→ ``target``（u64）。

    TDR：``@0 biguint target``，描述写的是「角色」（原表就这么写的，语义存疑）。
    所以调用方**不要**拿它当唯一依据，应优先用自己记录的进行中交互的 rid。
    """
    if len(body) < 10:
        raise ValueError("CS_PROTO_MO_CANCEL_INTERACT requires 10 bytes, got %d" % len(body))
    return struct.unpack_from(">Q", body, 2)[0]


def decode_pull_state_req(body: bytes) -> bool:
    """``CS_PROTO_MO_PULL_STATE_REQ``（msg_id=12）→ ``True``（body 只有 dummy）。"""
    return len(body) >= 3


def decode_rock_hit(body: bytes):
    """``CS_PROTO_MO_ROCK_HIT``（msg_id=7）→ ``(target_mid, hit_range)``。

    TDR 直读（``-m codec struct CS_PROTO_MO_ROCK_HIT``）::

        @0  target_mid  biguint  u64  目标 mid
        @8  hit_range   int      i32  受击范围

    ⭐⭐ **这条是「投石真的飞出去了」最硬的证据**：客户端只有在
    ``攻城器械射出飞行道具("武器_飞行.投石")`` 真的生成了飞行物、并且它撞到了东西
    之后，才会回这条 C->S 包。所以「松手后服务端发了 sel=6，但全库看不到 sel=7」
    ≡ 「客户端根本没生成投石」。

    ⚠️ 载荷里**没有飞行物 id**（``flyer`` 不回传）⇒ 想关联具体是哪一发，
    只能靠时间戳近似。
    """
    if len(body) < 14:
        raise ValueError("CS_PROTO_MO_ROCK_HIT requires 14 bytes, got %d" % len(body))
    target_mid, hit_range = struct.unpack_from(">Qi", body, 2)
    return target_mid, hit_range
