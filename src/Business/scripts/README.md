# Native 业务脚本边界

入口为 `app.py` 的 API_VERSION 1 / STATE_VERSION 2；状态仅由 plain
dict/list/str/int/float/bool/bytes 组成，连接和定时器由 native host 持有。
相同 schema 的 reload/rollback 保留角色、业务阶段、选将、CAMP、UNIFY
限速与 pending timer deadline，不重发初始化、不执行离房。

## 已实现的 fixture 合同

- `login` AUTH 后 25 秒 VERSION；`logic` AUTH 后 25 秒 LOGIN，再过
  20 秒 SYNC_LOGIN。固定虚构 user 10000 / actor MID 1，昵称按用户要求统一为
  GBK“吃我一记流星锤”；复用 canonical
  `protocol.py` 与 `login_flow.py` 的大厅资料、单武将卡和 formation。
- `logic` ROOM reconnect 非零响应、固定房间 1 / 资源 1028 的 create/enter；
  MATCH 保留原 listener 的有界 opaque pattern 回显、1 秒 result、2 秒
  instance notify。进入地址使用 native context 的 advertisedAddress/instancePort。
  ⚠️ **1028 是「洛阳死斗」的卡片 pattern**，不是关卡号；
  **不设 `T7_LEVEL` 时行为与改动前逐位相同**（见下面「关卡（换图）」一节）。
- `instance` AUTH 后 20 ms SYNC_LOGIN + 最小 UPDATE_INST；随后每步 200 ms
  GAME/ROUND 元数据；CAMP→固定 VISION/actor basic→SYNC_ITEM→LOAD_OK。
- instance BATTLE_HERO 容器（进图匹配面板的选将名单）**跟着大厅出战阵容走**：
  `contracts.py` 的 `HERO_IDS` 由名册 `battleFormation` 那几张卡的 resource id 生成，
  当前是赵云 110001、程普 1006、黄忠 3012、华佗 3031；槽位数 = 出战人数，
  报文长 `28 + 88×人数`。选择 1..N 时通知对应 hero，最终出战 VISION 使用最后选择。
  2026-09-21 之前这里写死 (110001, 110003, 110004)（赵云/关羽/孙尚香，照 VM 抓包的
  fixture），和大厅名册各走各的，所以出现过「外面出战四个、进图是另外三个」。
  大厅的单 HEROCARD/formation 维持原 PS 合同。
- LOAD_OK 建立共享 20 分钟准备窗（9 月 13 日已部署人工基线），1 秒后 actor
  state 5。PLAY 或准备窗第 1195 秒触发既有 state 6、VISION DEL/ADD、state 8；
  第 1195 秒 START、再过 5 秒 GAME，对战时长 90 分钟。
  重复选择不重置时钟。没有选择时留在待选状态，后续实际选择才恢复 START。
- VISION 附单武器 1030411，恢复固定敌方 actor MID 2、GET_OBJECTS 后
  NOTIFY_ACTIVE；恢复 START_PATTERN_DATA_NTF 和 SPAWN_AREA_STATE。
- 出战对象 state 8 后同步发送一次 MOVE selector38、state1 STOP，零轴向/速度；
  不再主动发开局 state10，也不创建 prime 定时链。重复 PLAY/round-start 不重复初始化。
  新会话STOP使用默认heading 0，不设置普通地面位置或移动起点；无GAME后新输入不推进。
  `battleEntered`仅表示角色就绪，`groundEnabled`由ROUND_STATE GAME打开，离房关闭。
  PREPARE与START中selector3/52仍检查支持的长度、heading/键值和首次位置有限性，
  合法消息静默消费，不缓存heading/按键、不回selector4/38、不启动ground-step。
  GAME后的新selector3才更新heading并回selector4 `MOVE_DIRECT_BC`；使用现有服务端位置
  （首次移动前用初始化位置），不采纳heading报文中的客户端坐标。移动中先按旧方向
  结算至处理时刻，再应用heading；静止不推进。不改变mask、速度、两阶段起点或周期截止。
  方向与 MOVE38 共用递增 server tick。
  selector4 客户端路径构造 PosCommand + DirCommand，不附动作/active/group；
  仍会校正位置，不能把它称为无位置报文或相机初始化修复。
  selector52 仅在角色就绪且GAME门打开后响应新WASD，复用
  canonical 投影，保持20Hz与50ms名义位移0.25，实际位移按elapsed计算并按Single舍入。
  下一截止沿原50ms网格推进；迟到只发一条最新状态，不补发历史包。单次最多结算100ms，
  超额时间记录并丢弃、不延后偿还；63ms正常推进约0.315。按键变化先结算旧方向，
  moving→moving不重排周期，释放/相反键先结算再STOP并取消timer。
  不重放准备阶段按键；NOTIFY_ACTIVE仍只是对象激活，不能代替GAME输入许可。
  这是兼容 fixture，不宣称原版速度、碰撞或仅靠STOP的相机初始化已恢复。
- 最终 actor state8、初始化STOP之后，同事件发送一次 BATTLE command4 / selector3
  `STATE_SYNC_SIMPLE`：inst_id=1、seq_no=1、state=2（原动作表“待机”）、state_time_ms=0。
  state_change_ms 使用真实 `nowMs-instanceStartedAt`，起点与 UPDATE_INST 通告一致，
  超出 UInt32 时整个事件回滚。state_time_ms=0限定首次进入的零持续时间候选，不伪造攻击时长。
  消息调用客户端原 Fightable 配置和 normal 选参，不下发组名，不使用 RUN 激活相机。
  保留 battleEntered 幂等；PLAY/round-start、heading、按键和同schema reload不重复同步。
  旧会话缺少实例起点时记录需新实例，不猜起点、不异步补发；新登录的新实例才保证完整初始化。
  state2资源语义与消费入口已核实；用户确认准备阶段选将后已可用鼠标转相机。
  该可见正例不是同对象descriptor/Direction提交或完整C0–C6验收；准确操作时刻未提供。
- 普通地面移动**已回退到实机基线之前的旧环形表实现**（2026-09-18 用户实机判定
  “WASD 恢复一下，还不如原来的”）。现行取值：
  `GROUND_WALK_STATES = {(-1,0):2,(-1,1):3,(0,1):4,(1,1):5,(1,0):6,(1,-1):7,(0,-1):8,(-1,-1):9}`、
  `GROUND_RUN_STATES = {(-1,0):10,(-1,1):11,(-1,-1):12}`、`groundMoveState()` 返回单个 int。
  一次“显示轴修正”曾被写入（`wireAxes()`：`wire_fb = W-S`、`wire_lr = D-A`，
  速度改成 current==maximum、慢走 1000/奔跑 5000），但用户实机反馈更差，已整体撤销。
  **因此现在 `broadcast()` 仍把世界积分轴（`local_fb = S-W`、`local_lr = A-D`）
  直接写进显示字段，W 得到 FB=-1000，`current_velocity/max_velocity` 恒为 5000/25000。**
  这一混用是已知问题，但撤销是用户的明确要求；在拿到新的实机对照证据前不要重新引入。
  离线回归 `t7_jump_verify.py` 第 2 组锁定的是“逐字节不变”，不是“语义正确”。
- **攻击状态下发**（`battle.py`）。此前 `command=4` 全无处理，64 条实机
  攻击报文落进 unhandled 兜底只记录不回包，故“不能打”。现已解出并驱动：
  * C2S 布局（TDR `CS_PROTO_BATTLE_BATTLE_MSG` + 实机校验，body 恰 11 字节）：
    `@0 u16 sel=1`、`@2 u32 key_comb`、`@6 i32 msg_id`、`@10 i8 is_auto_parry`
    实机样例：`00010000008c0004961a01` → key_comb=0x8C(下)、msg_id=300570、is_auto_parry=1。
    **wire 上的 msg_id 就是完整的 u32（300540 等），不要再叠加任何前缀。**
  * `key_comb` **是 `KEY_*` 枚举的位图**（旧描述“bit7=按下标志、低 4 位=方向”只是
    恰好成立的巧合，2026-09-18 已按全量实机配对推翻并替换）。客户端宏表给出位定义：
    bit0/bit1 = 鼠标方向两比特（0b00=下、0b01=右、0b10=上、0b11=左，故低半字节恒为
    0xC..0xF）、bit2/bit3 = KEY_ATTACK|KEY_MODE（所有动作共同置位）、
    bit7 = 左键、bit8 = 右键、bit9 = F、bit11 = E、bit14 = X、bit21 = SHIFT、
    bit22 = 双击W、bit26 = 快跑状态。宏表 `KEY_*` 枚举：0..28，`KEY_MAX=29`。
  * `is_auto_parry` 在 TDR 里是 `int8`、描述“是否为自动格挡”，即布尔标志而非常量。
    现行实现只做 `in (0, 1)` 的取值域校验。**注意：实机抓到的每一个包都是 1**，
    所以放宽到 {0,1} 是防御性处理，不是由实机证据驱动的；旧实现断言 `== 1`
    在本机从未真正触发过（会话 13496 的 26 条 ERROR 全部是 tuple 错误）。
  * 四向攻击意图：上 300560、右 300550、下 300570、左 300540；释放 300020
  * 四向招架意图：上 300580、右 300590、下 300600、左 300610；释放 300620
  * 静态拓扑（s_act_state_cli）——四向攻击 4 阶段：
    上 235→237→238→239→2；右 241→243→244→245→2；
    下 247→249→250→251→2；左 253→255→256→257→2
  * 四向招架 3 阶段（《Hero1101四向攻防恢复依据与实机门》第二节第 2 条）：
    上 305→223→227；右 308→224→228；下 307→225→229；左 306→226→230
  * 按住 / 松开语义（同文件第三节第 3 条）：左键**按住蓄力**、快速点击直接进过程；
    右键按住依次进入预备/蓄力/过程，**松开回待机**。落成
    `ATTACK_HOLD_INDEX=1` / `ATTACK_RELEASE_INDEX=2` / `BLOCK_HOLD_INDEX=2`，
    定时器走到 hold 索引即**停住**——这修的是实机反馈“按左右键自动松开了，不松开是蓄力”。
  * ⚠️ 解析释放报文（300020/300620）时必须按**已按下的 intent** 查链，
    **不能按释放报文自己的 id 查**：300020/300620 不在任何链里，按它查会得到
    `releaseIndex=None`，于是所有释放都被误判成招架直接回待机，攻击永远走不到过程态。
  * 经 `command4/selector3 STATE_SYNC_SIMPLE` 下发；seq 从 2 起（进场待机已占 1）
  * 门控用 `battleEntered`（角色就绪）而**不是** `groundEnabled`（GAME 开启）：
    实机日志显示攻击按下意图出现在 PREPARE 阶段（GAME 之前），用后者会全被挡掉
  * 动作未结束时拒绝覆盖新意图（避免状态竞争导致抖动/动作跳变）。
    ⚠️ **2026-09-19 加了唯一的例外：特殊键可以打断「正在蓄力的四向攻击/招架」**
    （实机规则「一直按左键蓄力的时候 同时也可以按 F 踢出去的」）。
    见下面「特殊键打断正在蓄力的动作」那条。拒绝日志现在带 `index=` / `reason=`。
  * **类型安全约束（曾导致 26 次整事件丢弃）**：`flow.session` 会被原生层序列化并做
    类型校验，写入 tuple 会让它报 `unsupported state type: tuple` 并**丢弃整个事件**
    ——表现是“按下了但既没日志也没下行”，且该事件的 state 也不会提交（后续 release
    于是看到 `battle-attack-release-without-press`）。故 session 内**只存标量**，
    链按 `intent` 现查 `ATTACK_CHAIN_BY_INTENT`/`PARRY_CHAIN_BY_INTENT`，不把链写进 session。
  * **各阶段 duration 的取值史**（四次变动，**当前停在第 2 版**）：
    1. 最初是 `200/400/700`——**我自己拍的占位值**，注释却写成"按实机反馈标定"，
       被用户当场点破（"哪的实机标定 ，，逆向的，还是哪的 比真实游戏 就是快"）；
    2. 2026-09-19 改成从客户端 `s_battle_param_cli.bin` 读的
       `[持续时间]预备/过程/结束` 中位数 = 360 / 700 / 500 ms
       ——**取法错了**（见下面那条复核警告），但**实机体感是这几版里最好的**；
    3. 2026-09-19 晚：把预备/结束改成过场拍 `SPECIAL_PASS_MS=1`，
       F 链 2193→1335ms、冲刺 2433→1934ms；
    4. ⚠️ **2026-09-19 凌晨：第 3 版被实机推翻并全部回退**
       （用户原话「这次的修改 全部回退一下，还没有上次效果好」，
       范围是 F 键和 SHIFT 键）。**当前代码 = 第 2 版**。
       回退记录见工作区 `rolledback-20260919/README.md`。
    四向攻防的 `ACTION_PHASE_MS=200` 是 2026-09-18 实机验收值，默认保留，
    改 `ATTACK_USE_TABLE_DURATIONS=True` 可切到表值 360/360/700/500。
    出处与离线复算见工作区 `T7-动作时长表值提取报告.md`。
  * ⚠️ **2026-09-19 复核：上面那两个中位数不是攻击行的表值，是借来的**
    （实机反馈「F 会卡一下 / SHIFT 滑得有点远」之后查出来的）。两条硬事实：
    1. **带 `[攻击类型]` 的行里，`[持续时间]预备` 出现 0 次、`[持续时间]结束` 出现 0 次**
       （全表非空 348 / 353 行，其中带 `[攻击类型]` 的 **0 行**）。也就是说
       **六种攻击动作只有「过程」一拍有表值**；360/500 是从**受击/防御/招架行**
       （键形如 `a=2 b=(1,1)`）借来的占位值。
    2. 冲刺攻击行另有 **`[移动时间]`（760/800/967/1033）与 `[移动初速度]`
       （2226/2500/2585/3950）**——**位移只持续「移动时间」那么久**，不是整个「过程」。
       脚踢攻击行**没有**这两个字段（前踢不产生位移）。
       **本模块没有实现这个位移模型**，所以「过程」多长客户端就滑多久 ——
       按 1933ms 全程推 vs 表里 800~1033ms，约 **2 倍**。
    ⚠️ **注意：知道"360/500 是借来的"不等于"改掉它就更好"。**
    第 3 版按这个判断把两拍压成 1ms，实机**反而更差**。所以现在**故意保留占位值**，
    这一条**不要再当 bug 去修**，除非有新的实机对照证据。
    逐拍时长可用环境变量临时覆盖（不改代码、不重启）：
    `T7_MS_KICK=360,1333,500`（回放当前基线做 A/B） / `T7_MS_DASH=1033,1`
    （试「位移窗口按 `[移动时间]` 截断」） / `T7_MS_SHIELD=1000` / `T7_MS_FANCY=1000`
    （拍数必须与链长相等，否则整条忽略；每拍必须 > 0）。
    详见工作区 `T7-F卡顿与SHIFT滑行-时长复核.md`。
  * **仍未闭合**：① “某武器 → 表里哪一行”的复合键映射（用的是名字匹配后的众数/中位，
    同一攻击类型在不同武器下能差 30%+）；② 攻击动作真实的预备/结束时长
    （可能在 `s_act_state_cli` 每行 10,950 字节里那 **7,742 字节未解码区**，
    也可能由客户端动画本身驱动）——**已试过一版"1ms 过场拍"，实机更差，已回退**，
    现在停在"用非攻击行中位占位"；
    ③ 攻击位移模型（`[移动时间]`×`[移动初速度]`）——**没有实现**，
    是实机「SHIFT 滑得有点远」的**候选**成因之一（另一个候选是 20Hz 地面回声，
    那套开关也已随本轮一起回退，见下）。
  * **仍未闭合**：命中窗口、`state_time_ms` 语义。
    ~~取消/打断规则~~ —— 2026-09-19 曾实现（`_canPreempt` / `T7_CANCEL_MODE`），
    **已随本轮全部回退**。当前行为回到"动作未结束就一律拒绝"，
    已知代价：按着左键蓄力时按 F 会被吞（全量扫 9 个会话有 20 条
    `battle-special-rejected`），**这是用户有实机对照后的选择，别改回去**。
  * 格挡成功/被格挡（`ACT_STATE_BLOCK_SUCCESS=94`、`ACT_STATE_BE_BLOCKED=36`、
    盾牌格挡成功 111）没有“某个格挡声明 → 具体 state”的 producer 映射，故仍只记录不驱动
- **特殊键战斗意图已解出并驱动**（2026-09-18 新增，`battle.py`）。
  这一段补上的正是《Hero72 待机移动攻防跳跃特殊键动作映射只读总表》第七节当时
  缺的那条证据——该节只看到 F/E/Shift 与视角包、左键意图混在同一时间窗，
  因此裁定“不能把包分别命名为 F、E、Shift”。现在 key_comb 的**位**与 msg_id
  在同一帧内一一对应，命名不再靠时间窗猜测：

  | 键 | key_comb 位 | 实测 key_comb | msg_id | 宏名 | 状态链 |
  |---|---|---|---|---|---|
  | F | bit9 | 525 / 526 | 300700 | `RES_MSG_FORWARD_KICK` 前踢 | 335→336→337→2 |
  | E | bit11 | 2062 | 300110 | `RES_MSG_SHIELD_ATTACK` 盾击 | 32→2 |
  | X | bit14 | 16396 / 16397 / 16398 | 300738 | `RES_MSG_SWORD_FANCY_WAVE` 挽剑花 | 517→2 |
  | SHIFT | bit21+bit26 | 69206030 / 69206031 | 700250 | `RES_MSG_DASH_ATTACK` 冲刺攻击 | 439→440→2 |

  状态号同样取自客户端宏表，与 msg 名一一对应：`ACT_STATE_FORWARD_KICK_PRAPARE/
  PROCESS/END`、`ACT_STATE_SHIELD_ATTACK`、`ACT_STATE_SWORD_FANCY_WAVE`、
  `ACT_STATE_DASH_ATTACK_PROCESS/END`。
  * **入口状态不再靠名字猜**（2026-09-19）：改读客户端 `s_act_state_cli.bin` 的
    `(source state, msg) -> target state` 转移表（MSES v7，row=10950、count=445、
    转移数组容量 100）。这张表写死了“客户端本机按这个键会进哪个状态”，而客户端本机
    预测表必须与服务端下发一致，所以它同时就是服务端的入口状态：

    | 报文 | 入口 | 入边数 | 说明 |
    |---|---|---|---|
    | 300700 前踢 | **335** 预备 | 61 | 有预备拍 |
    | 300110 盾击 | 32 | 63 | 单状态 |
    | 300738 挽剑花 | 517 | 37 | 单状态 |
    | 700250 冲刺攻击 | **439** 过程 | 3（来源 2/34/517） | **没有预备拍** |

    冲刺攻击这一条是本次唯一的行为改动：旧链写的 `438 ACT_STATE_DASH_ATTACK_PRAPARE`
    在**全表 445 行、约 1.9 万条非零转移里入边数为 0**，没有任何报文能进去；而 700250
    从待机(2)、盾牌防御(34)、挽剑花(517) 三处都直接落 439。也就是说冲刺攻击在客户端
    本机根本没有“预备”这一拍（预备就是按 SHIFT 快跑本身），服务端若先下发 438，
    客户端会播一段真实游戏里不存在的预备动画。
  * 同理，336/337（前踢过程/结束）与 440（冲刺攻击结束）入边同为 0——它们由
    **服务端定时器**推进，不走客户端本机报文表，与本文件的实现方式一致。
  * 特殊键**只有“按下”一个边沿**（客户端不发释放报文），故起点即 `released=True`，
    由 `ACTION_TIMER` 把整条链自动推完再回待机 2。
  * `_releaseOf(特殊键)` 返回 0（不是合法 msg_id），使任何释放报文都不匹配，
    落到 `battle-release-without-press`，**不会**被误判成招架释放而提前踢回待机。
  * 每个特殊键报文都会落一条 `battle-special-key key=… bit=… present=…` 诊断日志；
    `present=0` 不阻断驱动（msg_id 才是权威），只作为下一轮的比对证据。
  * **未闭合**：链内后续拍的**推进节奏**仍是服务端定时器行为，无法从该表读出；
    仍缺原服 STATE_SYNC 正样本。实机若不表现，优先怀疑这一层而不是 key_comb 位定义。
  * **未闭合（新发现）**：该表里并存**两代 act state 机**。本文件用的是旧代
    （state 1..~540，报文 3005xx/3006xx/3007xx）。另有一整套新代，入口是
    550 `ACT_STATE_NONE_BATTLE_WAIT` / 551 `ACT_STATE_BATTLE_WAIT`，报文号也换了一套
    （300763 NEW_DEFENCE、300765 LIGHT_ATTACK、300750 HEAVY_ATTACK、
    300769~300772 NEW_DODGE_*、700250 → 599 `ACT_STATE_NEW_DASH_ATTACK_PROCESS`、
    300751 GRAB_THROW）。2026-09-18 实机验收“基本可以”说明 T7 走旧代，但新代那批
    报文客户端是否也会发、什么条件下发，未闭合。实机若出现“某个键完全没反应”，
    优先回这张表查是不是落到了 550/551 那一支。

  各拍时长（**当前 = 2026-09-19 第 2 版；当晚那版"过场拍"已回退**）：

  | 意图 | 逐拍 ms | 合计 | 出处 |
  |---|---|---|---|
  | F 前踢 | **360** / **1333** / **500** | 2193 | 只有 1333 是攻击行表值（过程 = 1000/1333/1500 → 中位）；360/500 是**非攻击行**中位（占位值） |
  | SHIFT 冲刺攻击 | **1933** / **500** | 2433 | 过程 = 1400/1900/1933×8/2033×7 → 众数；末拍 500 是占位值 |
  | E 盾击 | **1333** | 1333 | 表内无盾击行，取脚踢中位作代理（整行都是代理值） |
  | X 挽剑花 | **1333** | 1333 | 表内无挽剑花行，同上（整行都是代理值） |

  * ⚠️ **不要"顺手修掉" 360/500**：它们确实不是攻击行的表值
    （表里带 `[攻击类型]` 的行，`[持续时间]预备/结束` 出现 **0 次**），
    但 2026-09-19 晚把它们压成 1ms 的"过场拍"（`SPECIAL_PASS_MS`）之后，
    **实机比原来更差**（用户原话「这次的修改 全部回退一下，还没有上次效果好」），
    已全部回退。**知道"它是占位值"≠"改掉它更好"**，这是这一轮最贵的教训。
    回退记录见工作区 `rolledback-20260919/README.md`。
  * **运行时覆盖**（默认不生效）：`T7_MS_KICK` / `T7_MS_DASH` / `T7_MS_SHIELD` /
    `T7_MS_FANCY`，逗号分隔逐拍毫秒，拍数必须与链长相等否则整条忽略。
    例：`T7_MS_KICK=1000,1333,500` / `T7_MS_DASH=1400,500` 可用来在实机上找值，
    调对了再回来改 `SPECIAL_BEATS_BY_INTENT`。
    四向攻防表值也解出来了（360/640~700/500），但 `ACTION_PHASE_MS=200` 是已验收行为，
    默认不动，`ATTACK_USE_TABLE_DURATIONS` 一行切换。
- **（已回退）特殊键打断正在蓄力的动作**（2026-09-19 加过，同日凌晨**整体回退**）。
  当时实现的是 `battle.py` 的 `_canPreempt` + `T7_CANCEL_MODE`，允许 F 在"左键蓄力中"
  抢占四向攻击。**实机反馈这一版不如上一版，已删掉。**
  当前行为回到 `if action["index"] >= 0:` 一律拒绝，拒绝日志形如
  `battle-special-rejected msg_id=300700 while running intent=300550`（**不带** `reason=`）。
  * 已知代价（**接受，别再改回去**）：报文里确实有 **15 条** `300700` 带
    `bit7(LMB)=1` 的样本（`0x0080028D`×11 / `0x0080028F`×2 / `0x0480028D` / `0x0480028F`），
    回退后这些 F 会被吞——全量扫 9 个会话有 **20 条** `battle-special-rejected`
    （新来 `300700` 正在跑 `3005xx`：`300550`×12、`300540`×7、`300570`×1）。
  * 保留这些数字是为了"以后想重做时不用重新逆向"，**不是**为了暗示该重做。
- **（已回退）战斗动作期间压制地面回声**（2026-09-19 加过，同日凌晨**定点移除**）。
  当时在 `controls.py` 加了 `battleBusy()` + `BATTLE_SUPPRESS_ECHO_ENABLED`（默认关），
  三条广播路径都接上，用来消掉"battle 说你在冲刺、move 说你在快跑"的两套权威冲突。
  **已随本轮一起移除**，`controls.py` 现在只剩空气墙那一组新增。
  62 项回归用例归档在 `rolledback-20260919/t7_battle_suppress_verify.py`。
  * 保留的**原始证据**（结论本身仍成立，只是不再驱动代码）：会话 `13496-253761705`
    第 1 次 SHIFT 冲刺（439 过程 1933ms + 440 结束 500ms，共 2433ms）期间，
    服务端发了 **49 条** `move-ground-periodic-position-echo`，
    **每一条**都是 `state=10`（跑-前）/ `current_velocity=5000` / `max_velocity=25000`，
    state 一次没变；位置照常每 50ms 推进 0.25 单位，2433ms 合计推进 **11.84 单位**。
    —— 也就是说**冲刺位移完全是客户端本地预测，服务端一点冲刺位移都没发**。
- **快跑 / SHIFT 加速**（2026-09-18 新增，`controls.py`）。
  客户端**不会**把快跑写进 `sel=52` 的 MOVE_KEY 位图——`E_MOVE_KEY_CATEG` 只有
  INVALID/WASD/CTRL/SPACE 四类，没有快跑类别。快跑走独立报文：
  * `E_CS_PROTO_MOVE_FAST_RUN_REQ = 63`「快跑请求」，TDR 结构只有一个字段
    `@0 int8 is_start`「是否开始快跑」，所以 body 恰好 3 字节（u16 sel + int8）。
    改动前它落到 `unhandled command=2 selector=63`，也就是“按了 SHIFT 服务端不认”。
  * 处理：把 `is_start` 存进 `session["ground"]["fastRun"]`（标量）；
    若角色正贴地面前向移动就**立刻补发一次** `move-ground-fast-run-state-echo`，
    让加速即时生效；空中不动地面速度（跳跃路径自己广播）；GAME 之前也照样记录。
  * 走/跑判定是**并集**而不是替换：`fastRun` 或原有“持续前向移动 ≥
    `GROUND_RUN_DELAY_MS`(2s) 自动进跑”。客户端不发 sel=63 时 `fastRun` 恒为 0，
    判断结果与改动前逐位相同（`t7_wasd_verify.py` + `t7_special_keys_verify.py`
    的回归门都锁这一条）。跑态表仍只有前向三格：`{(-1,0):10,(-1,1):11,(-1,-1):12}`
    ——符合“只有向前才能快跑”。
  * **未闭合**：sel=63 与 **SHIFT 单键**的绑定尚未由实机单键测试确认。9 个会话里
    **合计只出现 2 次**（还都在同一个会话内：`event=20181 is_start=1` /
    `event=20182 is_start=0`，hex `003f01` / `003f00`），且都落在 SPACE 按下与松开之间。
    因此实现只按报文自身语义
    （is_start）驱动，不假设它一定来自 SHIFT。`is_start` 的正文也无法从抓包直接
    读出（C2S 负载加密），故日志保留完整 hex 供离线比对。
- **首包位置的正常范围闸门 `anchorToSpawn()`**（`controls.py`，2026-09-22 加）。
  服务端**第一次**建立角色跟踪时（`ground["position"] is None`），只认客户端
  `CS_PROTO_MOVE_KEY_MSG`(sel=52) body `@12` 的三个 float；现在多一道校验：
  离 `wire.POSITION`（服务端在 `instance-ground-initial-stop` 里下发给客户端的那个出生点）
  平面距离 **> 150 m** 就丢弃、改用出生点，并记 `ground-position-reanchored from=... dist=... to=...`。

  为什么（会话 `9356-110117498`，同一个抓包里两条 instance 连接，可直接对比）：

  | | 首个带位置的包报的 `curr_pos` | 服务端随后回显 | 实机表现 |
  |---|---|---|---|
  | 连接 69（骑兵姜维） | `(0, 0, 0)` | 每 50 ms 发 `(0, -0.25, 0)` | 角色钉在世界原点：图外、天上、没地形，判「逃离战场，你已被处决」 |
  | 连接 70（步兵） | `(503.179, 581.889, 43.248)` | 跟着位移走 | 全程正常（跳跃/攻城器械都对） |

  两次下行 `instance-ground-initial-stop` 明文**完全一致**（都发 503.179,581.889），
  `actor play request expected SYSTEM type 1` 两条连接也都报 —— 所以既不是切图、
  也不是 ACTOR_PLAY 被拒造成的，是**客户端（骑马时）没把自己摆进场景**却把 0 报了上来。
  C2S 载荷是 method-3 密文（这个抓包里只有下行明文），客户端为什么报 0 尚未闭合；
  但服务端不该跟着把角色搬去原点，所以按图无关的半径拦下来 —— 骑兵在**所有图**都偏，
  正是这种共性问题的表现。只拦「还没建立跟踪」那一次，之后每步由 `advanceGround`
  积分，复活/传送不再经过这里。
- **⚠️ 骑兵不采纳服务端下发的出生点**（2026-09-22 三轮抓包对照，别再往出生点上找原因）。
  `anchorToSpawn` 只修**服务端自己那份**位置，客户端本地角色该在哪还是在哪：

  | 轮次 | 服务端下发（下行明文可解） | 客户端上行造成的服务端位置 | 实机 |
  |---|---|---|---|
  | `9356-109347746` 步兵 | `503.179,581.889,43.248` | 475 帧 `move-ground-heading-direct-bc`，位置正常漂移 | 正常 |
  | `9356-110117498` 骑兵 | `503.179,581.889,43.248` | 出现过 `(-1.8,-0.7)` ≈ 世界原点 | 图外、处决 |
  | `46668-140498449` 骑兵 | `494.305,584.875,42.880`（换过点） | 只有 2 帧、16 s 纹丝不动（钉在出生点=闸门生效） | 图外、处决 |

  把骑兵轮和步兵轮的**全部下行帧逐字节 diff**：结构完全一致，只差 actor id / 时间戳 / 坐标。
  * ⚠️ 这里原来写「坐骑 tid `1110221` **写死**在 `actor-vision-add-after-battle-confirm`
    偏移 `@294`、与玩家所选武将无关」—— **那句是错的**（2026-09-22 复查后删）。
    `mount_tid` 一直是 `contracts.actorVision()` → `battleLoadout(heroId)` 按**所选武将**
    从名册取的（2121 姜维 → 1110221，110001 赵云 → 0）；那个偏移是**帧**内位置，
    负载内是 `@282`，两处都对得上，只是当时把「按武将取」误读成了写死。
- **✅ 骑兵落点的当前假设：缺「坐骑视野实体」**（2026-09-22 接线，待实机）。
  依据不是推断，是 prior art 的**原版实网样本**：一批 `VISION_ADD_EVENT` 里是
  20 个 Actor + **1 个 Mount**，坐骑 `rid=21 / inst_id=0 / res_id=50001`，关联 `Actor rid=5`，
  而且**不在** `VISION_LIST_RSP` 的 1..20 里。客户端二进制里有失败分支
  `No Mount When Set Rider` / `No Mount Entity When LocalHero Get on Mount`，
  挂接规则是 `actor.mount_rid == mount.rid`。我们此前只给 `mount_tid`，
  `mount_rid`/`mount_inst_id`/`mount_attr_info`/`mount_move_data` **全 0**，
  也从没发过 `object_type=2` 的对象 ⇒ 客户端没有可摆的坐骑锚点，
  与「换出生点零收益、骑兵在**所有图**都钉在世界原点」对得上。
  * 已做：`codec/vision_flow.py` 新增 `encode_mount_vision_object()`（110 B =
    `object_type` 4B + `CS_PROTO_VISION_MOUNT_INFO` 106B，偏移按 `sh_proto_cs` 元数据，
    prior art 已用哨兵值逐个验过：`rid@0/inst_id@8/res_id@10/attr@14/move_data@26/
    filter_group@88/sub_system_group@92/mount_bm_data@96`）；
    `actorVision()` 只在名册带坐骑时把 `object_num` 1→2、在尾部 `svr_time` **之前**插这 110 B，
    并把 `actor.mount_rid` 写成同一个号。
  * 实测字节账（`c.actorVision(1, 2121)` vs 上一版编码器）：
    **步兵报文逐字节不变**；骑兵只有 2 个字节变（`@5` object_num、`@282` mount_rid 低位）
    + 尾部多 110 B；坐骑对象 `active=1`、`pos` = 本图出生点。`app.selfTest()` 仍 True。
  * ⚠️ `res_id` 用的是常量 `contracts.MOUNT_SCENE_RES_ID = 50001`（原版坐骑表 id，
    照夜玉狮子）。**它不是按武将查出来的** —— 正确来源 `s_mount_cli.bin`（50001~50033）
    这台机器上没有，`hero_roster.json` 里也扫不到 5xxxx 段。名册那个 `1110221` 是
    111xxx **装备/loadout 记录**号（prior art 明确把 1110231 当装备记录过滤掉），
    不能当场景坐骑资源用。
  * ⚠️ 坐骑 `attr`（12 B）与 `mount_bm_data`（10 B）仍留 0：元数据上它和 actor 的
    `attr_info` **同名不同结构**（actor = `CS_ATTR_NOTIFY`，坐骑 = `CS_PROTO_VISION_ATTR`，
    用错**不报错、值被静默丢成 0**），而真实 max hp 在 `s_mount_attr.bin`（data1.vfs
    第二块表，未闭环）⇒ 不猜数值。副作用：左下角马血条仍是黑的，可当验收副指标。
  * ⚠️ 别照抄 prior art 的结论：他们那例是「骠骑赵云**无马**站在地上」，
    我们这轮截图里**马是有的**（具装白马、人鞍都在），缺的是**落点**。
- **✅ 洛阳「骑兵专用出生点」已接线（2026-09-22 夜，开关 `T7_CAVALRY_SPAWN`，默认关）**。
  查实：洛阳现在用的出生点 `272.451,157.634,0.21751` 来自点组 `3379637679`，该组
  `is_infantry=1 / is_cavalry=0` —— **是纯步兵点**。同一张表
  （`D:\dfjq_out\map\005773_SH_RES_ACTOR_SPAWN_GROUP_TAB_c3059c0.xml`）里有
  **骑兵专用** born 组，一侧一个，正好对得上 camp：

  | 组 id | 点数 | 标志 | 本组最佳点（净空最大） | 段距 | groundZ 差 |
  |---|---|---|---|---|---|
  | `3796015618` | 4 | `is_born=1 is_revive=1 is_cavalry=1` **is_blue=1 is_red=0** | `258.730,135.621,0.107806` | **13.54 m** | 0.11 |
  | `1492739893` | 6 | `is_born=1 is_revive=1 is_cavalry=1` **is_red=1 is_blue=0** | `252.620,232.727,0.0950927` | **30.30 m** | 0.16 |
  | （对照）`3379637679` | 10 | `is_infantry=1 is_cavalry=0` 现在在用 | `272.451,157.634,0.2175` | 10.92 m | 0.001 |

  * 挑点判据 = `aairwall.xml` 的 `AirWall.footprint` **折线段最小距离**
    （不是 `<Entity Position>` 中心点距离；< 5 m 会被挤、≥ 10 m 才稳）。
    blue 组另外 3 点段距只有 9.34 / 7.27 / 4.27 m，**只有那一个够用**。
    工具：`hkx_decode/pick_cavalry_spawn.py`（现读表 + 现算段距，可复跑）。
  * 改法（`contracts.py`）：新增 `CAVALRY_SPAWN_BY_LEVEL`（10036 / 10005 两张，内容同）
    + `cavalrySpawnPoint()` + `_recomputeSpawn()`（**唯一入口**，营地换边与骑兵换点
    不再各写一份互相覆盖）+ `applyCavalrySpawn(heroId)`；
    `actorVision()` 开头自己调（三个调用点都覆盖到，且保证下发 pos 与服务端 POSITION 一致）。
  * **默认关**：`T7_CAVALRY_SPAWN` 不在 (1/on/true/yes) 里 → `applyCavalrySpawn` 三次早返回，
    **连 `_recomputeSpawn` 都不进**。实测 `actorVision(1, 110001)`（步兵赵云）报文
    md5 开关前后**完全相同** `786da5bda696`；骑兵姜维 `d0410f8b34e9` → `53139288ef09`，
    长度都是 585 B（只换位置，不动结构）。复跑：`hkx_decode/check_cavalry_spawn.py`。
  * ⚠️ **`camp` ↔ `is_blue`/`is_red` 客户端没实锤**。现在按「camp 1 = blue」填
    （依据：lysd 现在用的步兵组就是 `is_blue=1`，而 camp 没报时也用 `[0]`）。
    **实机若「骑兵在对面出生」，只把 `_CAVALRY_LYSD` 两个值对调**，别改别处。
  * ⚠️ 樊城（10002）**没有**骑兵点（`004558` 两组都 `is_cavalry=1 is_infantry=1`，分不开）
    ⇒ 这张图上开关是**空操作**，正好用来干净地验「缺坐骑视野实体」这个假设。
  * ⚠️ `T7_CAVALRY_SPAWN` 是**环境变量**，**双击 `T7.Server.exe` 吃不到** ——
    要在命令行 `set T7_CAVALRY_SPAWN=on` 之后再启动（同 `T7_LEVEL` 踩过的坑）。
- **✅ 骑乘族报文已补（35 / 31 / 43）**（2026-09-22 接线，**尚未实机**）。
  实机新证据：骑兵那局客户端**上行**了 `command=2 selector=42`
  （`MOVE_MOUNT_SUDDEN_STOP_REQ`，客户端语义表原话「cli->svr: 请求马瞬停」）
  ⇒ 客户端**已经进了骑乘态**，而服务端下发的移动帧**只有 38 号、且只寻址到骑手**
  （`target_inst_id=1`），坐骑实体（`inst_id=0`）从头到尾没收到过任何一条。
  三条都按这个缺口补，前两条只在名册带坐骑时发：
  * **35 `MOVE_MOUNT_BC`「坐骑移动广播」**（`controls.broadcastMount()`，跟 38 同一条
    链路、同一个 `position`）：TDR `CS_PROTO_MOVE_MOUNT_BC` 是 net_unit **39 B**，
    加 2 字节 selector ⇒ body **41 B**。字段
    `>HIHB` + 8×`h` + `dir` + 3×`f`。**⚠️ `dir` 是 float 弧度**，
    而 38 号的 `direction_yaw` 是 int16 **角度 −180..180**，两者**别混**。
  * **31 `MOVE_NOTIFY_ACTIVE` 指向坐骑**（`controls.activateMount()`，进场时发一次）：
    语义表原话「unactive 的时候，移动物体是**不能**在地图上进行位移操作的，
    但是不影响阻挡」⇒ 不发这条，上面 35 号等于白发。
  * **43 `MOVE_LOCK_ORIENTATION` 解锁**（`controls.unlockOrientation()`，所有武将都发）：
    `>HIBB` + selector ⇒ body **8 B**，`lock=0 / lock_camera=0`。
    格式**三方一致**（TDR net_unit 6 + 接收方 `0x00AB5660` + 执行方 `0x00B6AD40`，
    后者从不读 `lock_camera` ⇒ 恒发 0）；prior art 实机把它验成「能走 vs 不能走」的
    门禁，原版注释「正常服务器会在进场后解除角色方向锁」。
    ⚠️ **发送时机（进场一次）是兼容推断，不是原版证据。**
  * **骑乘状态枚举不再靠借**：`MOVE_MOUNT_BC` 的 `state` 走
    `controls._MOUNT_STATE_BY_GROUND_STATE` 查表，右列是**客户端主程序宏表直读**值
    （宏组前缀 `E_RES_MOVE_GROUND_*`，索引 4840..4915）：
    16..19 `MOUNT_LINE_UP_{1..4}_ACC_WALK` / 20..23 同族 `DEC` / 24 `MOUNT_LINE_DOWN_WALK` /
    26..29 `MOUNT_ARC_{1..4}_LEFT_UP_ACC` / 30..33 同族 `RIGHT_UP_ACC` /
    34..49 `DEC`、`MORE_DEC` 各档 / 50/51 `ARC_LEFT/RIGHT_DOWN_WALK` /
    **54 `MOUNT_STOP`** / 70 `DISMOUNT_WALK`。⚠️ 25 这个号在表里**是缺的**。
    马不能纯左右平移，所以纯左(4)/右(8)落到对应弧线档，后侧三档(5/6/7)落回退档 24。
    ⚠️ 这个**映射**（哪个步战档 → 哪个骑乘档）仍是我们定的，等实机再调。
  * **怎么自己把宏值读出来**（prior art 那句「宏表未解 ptrMacro=0」只描述**他们的工具**，
    我们自己的 `codec/tdr.py` 能读；下面这段是实跑过的）：
    ```
    cd /d/流星/T7/server && PYTHONIOENCODING=utf-8 python/python.exe -c "
    import sys; sys.path.insert(0,'.')
    from scripts.codec import tdr
    data=open(r'D:/刀锋铁骑/Bin/TieJiClient.exe','rb').read()
    blk=[b for b in tdr.find_tdr_blocks(data, source='TieJiClient.exe')
         if b.metalib_name=='sh_proto_cs'][0]
    print(tdr.parse_tdr_macro(blk, macro_name='E_RES_MOVE_GROUND_MOUNT_STOP').value)"
    ```
    ⇒ 打 `54`。要整段枚举就手工走表：`MACRO_COUNT@0x34`(=9503)、`MACRO_TABLE@0x4C`、
    描述符 `0x10` B、name@0 / value@4，指针都要过
    `tdr._relative_offset(d, raw, source=..., field_offset=)`。
    ⚠️ 宏名是 `E_RES_MOVE_GROUND_MOUNT_STOP`，**没有** `_STATE_` 那一段
    （只有组边界 `E_RES_MOVE_GROUND_STATE_MIN/MAX` 带），名字写错只报 `found 0`。
    顺带确认 TDR 类型号：1=VECTOR(12B) 2=uchar 3=char 5=int16 6=uint16 7=int32
    8=uint32 12=uint64 **17=float**。
  * 实测（离线桩跑 `controls.broadcast`）：步兵仍只发 `38`，**逐字节与上一版相同**
    （38 之后多一条 `43`）；骑兵发 `38 + 35 + 31(inst 0) + 43`，
    其中 35 的 `position` 与 38 的 echoed 位置一致。`py_compile` 干净，
    `app.selfTest()` 仍 True。
- **✅ 骑兵改成「服务端自己转舵」+ 跳跃带马**（2026-09-22 第二轮接线，**尚未实机**）。
  上一轮那三条上线后实机反馈：**前进后退可以，左右是平移过去的、脸还朝正前方**。
  抓包（`44792-158912374`，15:06 起那局，同卡同图）对上了：
  * 35 号发得没问题（W→`state 16`、A→`26`、D→`30`、停→`54`，位置与 38 号一致），
    所以**位移这一段协议不缺**；
  * 但 35 的 `dir` 全程 `0.0`、38 的 yaw 全程 `0` —— **朝向一次都没变过**，
    左右自然就成了横移；
  * 关键对照：**同一张卡、上一局（没有 35/31/43）客户端连续用 `cmd=2 sel=3`
    上报自己的朝向 341 次**（yaw 从 179 平滑扫到 −155，服务端照抄就行）；
    这局这条上行**一次都没有**（`grep 'move-ground-heading-direct-bc'` = 0）。
    ⇒ 一上马，朝向就归服务端算，而我们没算。
  * 改法（`controls.py`）：骑兵时 A/D **不再进位移投影**（`MOUNT_DRIVING_KEY_MASK`
    只留 W/S），改成 `advanceMountHeading()` 按 `MOUNT_TURN_RATE_DPS`（**120 °/s，
    手感常量**，原值在客户端坐骑配置里没读到）累加 `ground["heading"]`，
    再**用转后的朝向**算这一步 delta ⇒ 走的是圆弧。38 的 yaw 与 35 的 `dir`
    同一个值，自动同步。小数记在 `turnCarry`（朝向下行是 int16 度数，每步取整会把转角吃掉）。
    ⚠️ 符号约定：`project_standard_ground_step` 里 heading 增大 = 前进方向由 +x 转向 −y
    = 顺时针 ⇒ **D=+ / A=−**。实机若反了，只翻 `mountTurnKey` 那一个符号。
    ⚠️ 只按 A/D 时是**原地转**（不再有横移），要不要边转边前冲等实机再说。
  * 跳跃带马（`notifyJump` / `notifyInAir`）：以前原地起跳只有骑手有 55/39 号，
    马留在地上。现在每次跳跃广播都补一条 35（同 tick、同位置，`z` 已是抛物线上的值），
    54 号 `IN_AIR_STATE_BC` 也**按实体**各发一条（骑手 inst 1 / 坐骑 inst 0）。
    进场那条 `notifyInAir(0)` 因此也会顺带发坐骑一条。
  * 离线仿真门（改这条路径后要重跑，退出码非 0 就是回归）：
    `cd /d/流星/T7/server && PYTHONIOENCODING=utf-8 python/python.exe scripts/verify_mount_turn.py`
    ⇒ 全 OK：步兵五种按键组合朝向恒 0、按 W 走满 5.00 m（与改前逐位相同）、
    一条坐骑报文都没有；骑兵按 D `heading 0→120`、按 A `0→-120`（38 的 yaw 同步）、
    W+D 走圆弧 `(+1.88,-3.69)`（4.14 m < 直线 5.00 m）；
    原地跳骑兵 14 条 35 号、坐骑 `z` 6.861→7.436，步兵 0 条。`selfTest` True。
- **✅ 第三轮：38 号也换骑乘档 + 停发 35 号 + 转舵翻符号**（2026-09-22，**尚未实机**）。
  第二轮上线后的实机反馈：**「WS 前进后退是反的，AD 不对」**。同时回答「骑兵这套逻辑
  在本地客户端找不到吗」这个问题，把客户端主程序的移动接收函数全清了一遍：
  * **客户端只有 15 个 `GeRecvMove*` 函数，没有任何带 `Mount` 的**
    （`GeRecvMoveBc@0x10fcd60` / `GeRecvMoveNotifyActive@0x10fcebc` /
    `GeRecvMoveDriveEnableBC@0x10fcf38` / `GeRecvMoveActorWithJumpBC@0x10fcf60` /
    `...WithGroupActiveBc` / `...WithAnimationBc` / `...DirectBc` / `...StopBc` /
    `...DirControllNotify` / `...LookAtNotify` / `...InAirDirectBC` /
    `...ActorBatchBC` / `...CarrierStateBC` / `...ChangeLayerBC` / `...ClimbNotify`）。
    怎么清的：`grep -a -o 'GeRecvMove[A-Za-z_]*' TieJiClient.exe | sort -u`。
    ⇒ **35 号 `MOVE_MOUNT_BC` 在这个客户端版本里大概根本没有入口**，发了也没人接；
    坐骑本来就是按 `actor.mount_rid == mount.rid` 挂到骑手身上、跟着骑手走的
    （见 `匹配进游戏控制武将全套原版记忆_20260917.txt` 第六节）。
    所以加了开关 **`controls.MOUNT_BC_ENABLED = False`**（编码器、字段说明、`_MOUNT_STATE_BY_GROUND_STATE`
    查表全留着，实机证明马确实需要它就把这一个值改回 `True`）。
    ⚠️ 这是「二进制里没有对应符号」的**否定证据**，不是原版实网抓包；如果接收函数被
    strip 掉过名字，结论就得推翻。
  * **38 号（骑手本人）的 `state` 换成骑乘档**。那族 `E_RES_MOVE_GROUND_MOUNT_*` 宏
    本来就是给「身上有马的 actor」用的，继续喂步战 1..15 会让客户端按**步战状态机**
    演横移 —— 这正是「左右是平移过去的」的另一个可能成因。
    实现：`broadcast()` 里 `wireState = _MOUNT_STATE_BY_GROUND_STATE.get(state, state)`，
    **只喂编码器**，`state` 形参本身保持步战档（`broadcastMount` 还要拿它查表；
    上一版在这里就地覆盖，害得 `MOUNT_BC_ENABLED=True` 时 35 号查表落空、一条都不发 ——
    已经修回来了）。
  * **转舵符号翻了**：`mountTurnKey` 现在是 **A=+ / D=−**（原先按投影基向量推的是 D=+）。
    ⚠️ 这一条纯粹是被「WS 反了」逼出来的经验校正，`project_standard_ground_step` 的
    推导仍然说 heading 增大是顺时针。若这局变成「按 A 往右拐」，再翻回来，只改这一处。
  * 回归门（两种开关取值都跑过，退出码 0）：
    `cd /d/流星/T7/server && PYTHONIOENCODING=utf-8 python/python.exe scripts/verify_mount_turn.py`
    ⇒ 步兵四种组合朝向恒 0、W 走满 5.00 m、`state` 仍是步战 2/8/4/1、0 条坐骑报文；
    骑兵 W→`state 16`、D→`heading -120 / state 30`、A→`+120 / 26`、W+D 圆弧 4.14 m；
    `MOUNT_BC_ENABLED=True` 时骑兵每局 21 条 35 号 + 原地跳 14 条（`z` 6.861→7.436）。
    `py_compile` 干净、`app.selfTest()` True。
- **✅ 第四轮：骑兵重做 —— A/D 彻底不进位移投影**（2026-09-22，**尚未实机**）。
  实机反馈「现在骑兵方向都是乱的」。读自己代码抓到的根因**不在**换档，在**投影**：
  地面定时器 `controls.timer("ground-step")` 那一拍是
  `project(ground["mask"], heading)` → 不 moving 就 return → `advanceGround()`
  （**这一步里朝向已经转过**）→ 再拿**转之前、没摘 A/D** 的投影发 38 号。
  于是按住 W+D 时每 50 ms 一条「步战横移档 + `left_right=±1000` + 新 yaw」，
  客户端一边演横移、一边被我们转脸 ⇒ 又转又平移，就是「乱」。
  * 改法：投影收成**唯一入口**。算位移一律走 `drivingProjection()`（骑兵在里面
    `mask & MOUNT_DRIVING_KEY_MASK` 摘掉 A/D，步兵逐位不变）；这一拍要不要继续跑
    定时器走 `stepMoving()`（**有位移 或 正在转舵**）；上线档位走 `moveWireState()`
    （步兵=步战档 / 骑兵=骑乘档）。`broadcast()` 从此**原样发**调用点算好的 `state`，
    第三轮那张 `_MOUNT_STATE_BY_GROUND_STATE`（步战 1..15 → 骑乘档）**删掉**——
    两边都拿对方的编号当键查，那才是「`MOUNT_BC_ENABLED=True` 时 35 号查表落空」的真因。
  * 骑乘档改成由**按键组合直接选**（`mountMoveState`）：前/后 × 转舵方向 × 速度档，
    档号偏移 = `mountSpeedTier()`（我们积分出的 5.0 m/s 落在 `坐骑.psheet` 轻骑
    2/4/7/10 的第 2 档 ⇒ 偏移 1）⇒ 前进 **17**、前左弧 **27**、前右弧 **31**、
    后退 **24**、原地转 **54**。后退那族（24/50/51）原版**没有**速度分档；
    新常量 `MOVE_GROUND_MOUNT_STATE_ARC_{LEFT,RIGHT}_DOWN_WALK = 50/51`（宏表直读）。
  * ⚠️ 两件事仍**没有**被实机回答：原地转（只按 A/D）只能给 54 + 变化的 `direction_yaw`
    （原版骑乘状态里没有「原地踏步转」这一档）；转舵符号 A=+ / D=−（`mountTurnKey`）。
  * 回归门（`MOUNT_BC_ENABLED` 两种取值都跑，退出码 0）：
    `cd /d/流星/T7/server && PYTHONIOENCODING=utf-8 python/python.exe scripts/verify_mount_turn.py`
    ⇒ 新增两条硬判据：骑兵**每一拍 `left_right` 必须为 0**、`state` 必须落在合法骑乘档集合。
    步兵四种组合仍与改前逐位相同（2/8/4/1、W 走满 5.00 m、0 条坐骑报文）；骑兵
    W→17 / D→54 且 heading −120 / A→54 且 +120 / W+D→31 圆弧 4.14 m / S→24。
    `py_compile` 干净、`app.selfTest()` True。
- **✅ 2026-09-23：C 键下马协议已抓到并接线（当前生产开关 `mount_unride=state`）**。
  之前文档里「C 只有地图物件交互 / 没有下马通路」的说法已被新证据推翻，明确更正如下：
  * 最新会话 `13976-239667435` 中，骑马按 C 上行 **`cmd=4 sel=13`**，即 TDR
    `ENM_BATTLE_CMD_UNRIDE_MOUNT` / `CS_PROTO_BATTLE_UNRIDE_MOUNT`「C->S 下马通知包」；
    body 14B、15 条逐字节相同：`000d001af55400b0480b4f84ebd0`。
    TDR 字段是 `actor_unride_pos: PROTO_VECTOR@0`（12B）；这 12B 的实际坐标编码未闭合，
    当前不把它强行解成 3 个 float，而采用服务端当拍自己的 `ground["position"]`。
  * 接线位置：`app.py` 在 `controls.message()` 后调用 `controls.mountCommand()`；
    `cmd=4 sel=12`（上马通知，`mount_rid: biguint@0`）也预留了处理。
  * 下马第一次将 `session["dismounted"] = True`，骑手的 38 号切回步战 STOP/移动；
    独立坐骑仍继续收到 35 号 `state=54`，并锁在下马瞬间的 `mountPark`，避免坐骑实体
    因停发 35 号而未初始化或跟着步兵跑。客户端未收到确认时会重复发 sel=13，处理是幂等的。
  * `mount_unride=off` = 回到旧的 `unhandled command=4 selector=13`；`state` = 不发新
    cmd=7 字节的最小改动；`rsp` = 额外发 `cmd=7 sel=3 MOUNT_RIDE_RSP`。cmd=7 在全部历史
    会话中为 0 次，`CS_PROTO_MOUNT_RIDE_BC.map_pos` 是 8B biguint 且无样本，所以默认不猜 BC。
  * 离线门 `scripts/verify_mount_turn.py` 已覆盖：C 后骑兵/步兵隔离、38 号步战档、35 号
    停机位、重复 sel=13/12 幂等、`off` 原样、`rsp` 回读、session JSON 安全；当前 `ALL OK`、
    退出码 0。**这只是服务端线与报文结构已闭合，客户端画面上“人是否真的下马”仍待实机确认。**
- **跳跃下行链已实现**（`controls.py` 的 jump 段 + `codec/move_flow.py` 两个新编解码器）。
  实机事实：`key_categ=3`(SPACE) 报文确实到达服务端（`keys[5]=space_state`，
  由 TDR `CS_PROTO_MOVE_KEY_MSG` 字段表确认），旧实现把它当垃圾丢弃 → 已修。
  客户端**从不**下发 `CS_PROTO_MOVE_JUMP_REQ(sel=12)`（也从不发 sel=19/20 的
  SQUAT/UNSQUAT_REQ），所以服务端只能以 sel=52 的 SPACE 边沿作为触发。
  * ⚠️ **`jump_animation` / `squat_animation` 是枚举索引，不是布尔。**
    2026-09-18 实机反馈“按空格变成蹲下了、起不来了”的根因就在这里：旧实现发
    `jump_animation=1`，而 `MOVE_STATE_DATA_ANIMATION` 里 **1 = SQUAT（下蹲）**、
    **4 = JUMP（起跳）**。取值表（用 `codec/tdr.py` 的 `parse_tdr_macro` 从
    `TieJiClient.exe` 的 sh_proto_cs 宏表**实测**，不是推断）：

    | 值 | 宏 | 含义 |
    |---|---|---|
    | 0 | `MOVE_STATE_DATA_ANIMATION_NONE` | 无动画 |
    | 1 | `MOVE_STATE_DATA_ANIMATION_SQUAT` | 下蹲 |
    | 2 | `MOVE_STATE_DATA_ANIMATION_END_SQUAT` | 下蹲结束 |
    | 3 | `MOVE_STATE_DATA_ANIMATION_SUDDEN_STOP` | 马撞停 |
    | 4 | `MOVE_STATE_DATA_ANIMATION_JUMP` | 起跳 |
    | 5 | `MOVE_STATE_DATA_ANIMATION_JUMP_LAND` | 着陆缓冲 |
    | 6 | `MOVE_STATE_DATA_ANIMATION_END_JUMP` | 结束着陆 |

    `controls.py` 已把这张表落成 `MOVE_ANIMATION_*` 常量（0..6），
    `t7_jump_verify.py` 第 11 节回读客户端宏表做回归。
  * 现行链路（SPACE 按下沿，阶段机 `phase`：AIR → LAND → END → IDLE）：
    1. 起跳：`sel=54 IN_AIR=1` + `sel=55(jump=1, anim=4)` + `sel=39(jump=1, anim=4)`
    2. 滞空：起 `jump-step` 定时器，按 `GROUND_STEP_MS` 半隐式欧拉积分 z，
       每步发一次 sel=55(jump=1, anim=4) 把高度经位置字段**回流**给客户端
    3. 落地：`sel=55(jump=0, anim=5 着陆缓冲)` + `sel=39` + `sel=54 IN_AIR=0`
    4. 落地尾：`sel=55(jump=0, anim=6 结束着陆)`
    5. 收尾：`sel=55(jump=0, anim=0 NONE)` 并清 `active`
    **收尾必须回到 NONE(0)**，否则角色会停在某一帧上（旧实现的“起不来了”）。
    端到端实测序列：`4 ×11 → 5 → 6 → 0`，13 拍，无残留定时器。
  * 两个编解码器已按 TDR 偏移逐字段对齐并与真实抓包逐字节比对（35/48 字节）。
  * **未闭合**：`JUMP_INITIAL_VELOCITY=5.0`、`JUMP_GRAVITY=18.0`、
    `JUMP_LAND_MS=200`、`JUMP_END_MS=200` 是按走速量级推的
    **占位值，不是原服数值**；跳跃广播里的 `state` 取值（现沿用地面走/跑 state 或 STOP=1）、
    `jump` 是否也只是布尔、落地高度的物理语义、以及“sel=39 是否需要在滞空期间也每步下发”
    均未闭合。现在只在起跳/落地边沿附带 sel=39。
- 客户端另有 `command=2 selector=63`（快速奔跑请求）上行且无处理，仅记录。
  实机观察：它在 SPACE 按下后约 215 ms 出现（两次一致），与 SPACE 的因果关系未闭合。
- HEART_BEAT 精确回显 sequence/client timestamp；UNIFY_TIME 250 ms 限速，
  首个 RTT=0、后续非零；instance 每 2 秒 TIME_NTF。
- SYNC_LOGOUT 在 instance 上发送 END/FINISH_GAME/LEAVE_INSTANCE，250 ms
  后 LOGOUT_RSP；取消当前业务 timers。USER_GROUP QUIT 原样回显 guid/type。
- `research:diagnostic` 只记录业务阶段、连接、actor/选择标志和 pending timer，
  没有修复、重建或进场副作用。未定义 correction bundle，`apply-current` 明确报错。
- **空气墙碰撞已接线**（2026-09-19 新增，`controls.py` + 新的 `scripts/airwall.py`）。
  先摆事实，因为这件事一直被误解：

  * 客户端上行**没有位置包**。`uplink-diag.log` 全量清点只有 `cmd=2 sel=3`（朝向，
    `>h` 在 offset 7）、`cmd=2 sel=52`（WASD 按键位图）、`cmd=2 sel=63`（快跑）、
    `cmd=4 sel=1`（战斗）、`cmd=16 sel=3` 这几类。**位置完全由服务端积分**。
  * 那个积分的入口就是 `advanceGround()`，而它接线之前只有
    `delta = (distance*(-normFB*cos+normLR*sin), distance*(normFB*sin+normLR*cos), 0.0)`
    —— 输入只有 WASD + heading + step_distance，`delta[2]` 硬编码 `0.0`，
    **一行碰撞都没有**。所以「穿墙」永远是服务端的锅，不是客户端拦不住。
  * 碰撞数据在客户端 `map.vfs` 的 `../data/scene/map/<场景>/aairwall.dat`，
    是 **GB2312 纯 XML**。已抽取 62 个场景落到 `server/data/scene/<场景>/aairwall.xml`
    （62 个里有 3 个是**真的空文件** `<Entities/>`：`select_hall` / `td_map_e_1v1` /
    `xl_map_g_giant`，所以实际有墙的是 59 个）。

  接线方式与 **`fastRun` 同一套「并集而不是替换」**：

  * `AIRWALL_COLLISION_ENABLED = False` —— **默认关闭**。关掉时 `advanceGround()`
    的位置写入路径、日志、session 形状**逐位不变**，已实机验收的 WASD 基线
    （`t7_wasd_verify.py` 187 项）不会被这次改动碰到。
  * `AIRWALL_SCENE` —— 2026-09-19 晚**改成由关卡查表**：
    `wire.SCENE_BY_LEVEL.get(wire.LEVEL_ID, "")`，
    **没设 `T7_LEVEL` 时仍是空串**（不加载、不碰撞，记
    `airwall-disabled reason=no-scene`），与改动前逐位相同。
    这填掉了一个老坑：以前这个常量是**手动猜**的 `city_temp_low`，
    而实际进的是洛阳死斗（场景应为 `lysd`），**两个一直对不上**。
    `T7_AIRWALL_SCENE` 环境变量仍可单独覆盖（优先级最高）。
  * `AIRWALL_HEIGHT_FILTER = False` —— 墙自带竖直高度区间，理论上「墙顶低于角色」
    不该阻挡；但服务端的 Z 是**占位值**（`ground["position"][2]` 一直停在 0.218，
    而 `city_temp_low` 的墙整体长在 z≈100.7..121.0），一开过滤就**全放行**。
    所以默认关：先只按 XY 判定（未闭合）。
  * 不想改代码做单次实机验证时用环境变量：`T7_AIRWALL=1` / `T7_AIRWALL_SCENE=<场景>`
    （`0` 可强制关掉，只影响本进程，重启即恢复）。
  * 撞墙行为：**不推进位置** + 落 `move-blocked-airwall scene=… seg=… from=… to=…` +
    把这一拍下行压成 `state=STOP`、速度归零（`move-blocked-airwall-stop`）。
    定时器**不取消** —— 玩家一转身，下一拍 delta 不再命中就自动恢复。
  * 快跑 sel=63 那条路径**不经过 `advanceGround`**，所以单独用
    `groundBlocked(ground)` 守卫：贴着墙按 SHIFT 只发 STOP，不再喂一次「移动中」。
  * **未闭合**：① ~~场景名绑定~~ **已由「关卡换图」那节填上**（按 level 查表）；
    ② Z 维度（同上）；③ **没有碰撞半径**，
    命中即「停在原地」，不做墙面吸附/滑行/投影；④ 只管空气墙，
    `acollisionmap.dat`（Havok 二进制 4.5MB）里的地形碰撞**没做**；
    ⑤ **樊城(`tszz`)的空气墙基本是空的**——只有 5 段墙、全在出生点 120 单位外，
    从出生点向 72 个方向做射线**一个都没命中**。城墙本体的碰撞不在这份数据里。
- **关卡（换图）已接线**（2026-09-19 晚新增，`contracts.py` + `app.py`）。
  服务端原来把地图**焊死**在洛阳死斗上：`app.py` 硬校验 `resource_id == 1028`、
  出生点硬编码 `272.451,157.634,0.218`、空气墙场景写死空串。现在这三处一起跟 level 走。

  * ⚠️ **两个 ID 空间，别混**：
    `pattern_id` = 客户端大厅卡片带的（`ROOM_CREATE_REQ.resource_id`）；
    `level_id` = 客户端 `game_level` 表里的关卡号，也是 RSP 该回显的。

    | 图 | pattern（卡片） | level（关卡） | 模式 | 场景目录 | 主出生点 |
    |---|---|---|---|---|---|
    | 洛阳死斗（**默认**） | 1028 | **10036** | 10 实战训练 | `lysd` | `272.451, 157.634, 0.218` |
    | 宛城之战教学 | 29 | **10085** | 1 教学模式 | `pve_gc` | `430.000, 490.000, 20.4581` |
    | 樊城（攻城） | 20001 | **10002** | 3 攻城模式 | `tszz` | `503.179, 581.889, 43.2482` |
    | 玉门关外（会战/资源战） | 10002 / 50002 | **10062** | 12 会战 | `hz_map_b` | `429.429, 360.939, 6.86067` |

    ⚠️ 2026-09-22：**空气墙净空一律按 `airwall.AirWall.footprint` 折线段最小距离算**，
    别拿墙 `<Entity Position>` 中心点距离 —— 中心可以离墙脚很远（玉门关外副点两种口径
    32.72 m vs 3.90 m，差了 8 倍，差点把「贴墙」当成「安全」）。判据 < 5 m 会被挤、≥ 10 m 稳。

    ⚠️ 2026-09-22：樊城主区 `3935066568` 的 `pos` 实测离**复活组** `1805636849`（`is_revive=1`）
    只有 2.1 m、离出生组 `3494155748` 有 9.4 m，看着像「开局站在复活点上」，一度换成出生组的
    `494.305,584.875,42.8802`（净空 63.07 m、碰撞格空、groundZ 43.62）。**已退回区 `pos`**：
    实机轮 `46668-140498449` 证明换点对骑兵零收益（骑兵不采纳服务端下发的出生点，见下面
    「骑兵不采纳出生点」），只是多一个变量。区 `pos` 是步兵实证能用的值。

    ⭐ **2026-09-22 晚：发哪个营地由 `camp` 决定，不看 camp 才是「跑到外面」的根因。**
    樊城在客户端表里是**两个 `is_main=1` 主营地**：区 `3935066568`（`init_state=2`，
    `503.179,581.889,43.2482`）和区 `3935066570`（`init_state=1`，`622.006,572.036,44.4526`）。
    旧代码 `POSITION, ENEMY_POSITION = SPAWN_BY_LEVEL[LEVEL_ID]` 是**模块级常量**，
    `actorVision(camp, …)` 拿到 camp 只用来填 camp 字段、选点恒用 `[0]` ⇒ camp=1 的人
    被放进 `init_state=2` 那侧。中央要塞空气墙在 x 547..613，两营地分列 x=503 / x=622、
    **相隔 119 米**，所以开局像是站在图外。
    * **camp 怎么读出来的（方法，可复跑）**：C2S 是 method-3 密文读不到，但
      **S2C 是明文** —— `capture` 的 `frames-1.jsonl` 每条有 `offset`/`wireLength`，
      按偏移切 `frames-1.bin` 就能拿到字节。`instance-camp-exchange-notify`
      （`>HQiQi`，第 5 个 int）与 `instance-camp-choose-result-zero`（`>Hiiii`，第 3 个）
      都带 camp。实测 `44792-181725929` **樊城 21:19 和洛阳 21:25 两轮 camp 都是 1**。
    * 改法（一处表 + 一处调用）：`contracts.SPAWN_INDEX_BY_CAMP = {10002: {1: 1, 2: 0}}`
      + `contracts.applyCampSpawn(camp)`；`scene.py` 在 `instance-camp-choose` 解析出 camp
      后调用，换了边就记 `spawn-side-chosen camp=… level=… position=…`。
      ⚠️ **只在客户端真的报了 camp 之后才换**，没报就保持 `[0]/[1]` 原序（不替它猜）；
      不在表里的图一个字节都不改（洛阳那两个点只差 1.6 m，是**同一组散点**不是两边）；
      `applyLevel` 会按已知 camp 重选一次，切图不会退回错的边。
    * ⚠️ `init_state`↔`camp`（1↔1、2↔2）是**按数值推的，客户端没实锤**。实机若变成
      「开局在对面」，只把 `SPAWN_INDEX_BY_CAMP[10002]` 两个值对调。
    * 顺带查出（未改）：洛阳那个 `272.451,157.634,0.217519` **不是任何区的 `pos`**，
      是从点组 `3379637679`（`005773_SH_RES_ACTOR_SPAWN_GROUP_TAB`）抄来的第 1 个散点，
      该组 `is_born=1 is_infantry=1 `**`is_cavalry=0`**。同一张表里其实**有**骑兵专用
      born 组：`1492739893`（6 点，主区 `2592267309` 名下）、`3796015618`（4 点）。
      ⇒ 早先「骑兵没有专属点可填」这条**对洛阳不成立**。

    出处：客户端 `pattern_level_map.csv` / `game_level_table.csv`
    （`t7_level_verify.py` 会**现读这张 CSV 对表**，不是手抄数字）。
  * **切图不改代码**，两条通道，优先级 **环境变量 > 配置文件 > 默认**：
    1. **推荐**：编辑 `server/level.ini`（记事本就能改）
       ```ini
       [level]
       id=10085
       ```
    2. 命令行临时切：`set T7_LEVEL=10085` —— ⚠️ 必须在**启动服务端之前** set，
       **双击 `T7.Server.exe` 吃不到环境变量**。2026-09-19 第一次实机就是这么翻的车：
       点了两次宛城全被拒，日志里 `level=10036`（环境变量没进进程）。
    3. `T7_LEVEL_INI=off` 可完全不读 ini（回到「环境变量 + 默认」）。
  * ⚠️ **改完必须重启 `T7.Server.exe`**（脚本只在启动时读一次），并**重进图**。
    启动时会打一行
    `[contracts] level=… source=… scene=… spawn=… echoMode=… echo=… pattern=…`——
    **`source=` 就是判据**：`ini` / `T7_LEVEL` / `default`。看到 `default`
    就说明配置没读到（2026-09-19 **四次**实机点击都栽在这：两次是环境变量
    没进进程，两次是**进程比 level.ini 老**——ini 是 10:14 建的，
    服务端进程从 10:05 起就没重启过）。
  * ⚠️ ini 查找是**从脚本所在目录逐级向上**最多 8 级。
    因为服务端会把脚本快照到 `server/data/<会话>/revisions/t7rev_<hash>/` 再跑，
    那条路径下没有 ini；只取 `__file__` 的上一级会**重启了也读不到**。
  * **默认逐位不变**：不设 / 空串 / 纯空格 / 非法值 → 全部退回 `10036`，
    此时 `RESOURCE_ID` 仍是 `1028`、出生点仍是 `272.451,157.634,0.218`、
    空气墙场景仍是 `""`，而且 `acceptsResourceId()` 仍只认 1028。
    空串**不刷警告**（`export T7_LEVEL=` 在 shell 里很常见）。
  * **切了图之后不校验客户端卡片** —— `acceptsResourceId()` 恒真，客户端点哪张卡都按
    配的关卡走。这样「到底该回显 pattern 还是 level」这个未闭合项可以先绕过去做实验。
  * **`echo=client|level|pattern`（2026-09-19 新增）**：ROOM_CREATE_RSP 里那个
    `resource_id` 回显什么。**默认 `client`** —— 原样回客户端发来的卡 ID。
    **实机定论**：洛阳能进是因为 client 发 1028、server 回 1028（match=1）；
    其他 mode 之前 match=0 → 「连接房间服务器失败」。**默认 `client` 把洛阳的
    成功路径复用给所有 mode**（宛城发 29 → 回 29；樊城发 20001 → 回 20001；
    人机/攻城同理）。`echo=level` / `echo=pattern` 留作 A/B 备选。
    ```ini
    [level]
    id=10085
    ; echo=pattern     ; 想要的话回显 21/20001
    ; echo=level       ; 想要的话回显 10085/10002
    ```
    也可临时覆盖：`T7_LEVEL_ECHO=pattern`。
    ⚠️ **不切图时这个开关完全无效**（`RESOURCE_ID` 恒为 1028）—— 逐位不变是硬底线。
  * ⭐ **按「点到的卡片」自动定图（2026-09-19，用户要求）**：以前服务端只看
    `level.ini` 里手填的 `T7_LEVEL`，**不管客户端点的是哪张卡** —— 「配了宛城
    却点了攻城」就发宛城，客户端卡在载入地图。现在用客户端
    `pattern_level_map.csv`（权威）反查 card → level，建房时自动切：

    | 点到的卡 | 关卡 | 场景 |
    |---|---|---|
    | 宛城之战教学 `29` | 10085 | `pve_gc` |
    | 攻城/樊城 `20001`、排位赛樊城 `60005`/`61005` | 10002 | `tszz` |
    | 洛阳死斗 `1028`、洛阳高级训练 `18111` | 10036 | `lysd` |

    出生点、空气墙场景、回显 ID **三样一起走**。⚠️ **只在关卡真的变了时才动**，
    洛阳默认路径（点 1028 → 10036，同一关）不触发任何赋值 → 逐位不变仍成立。
    切图时日志会打 `level-auto-switch kind=switch card=... -> level=... scene=...`。

    ⚠️ **新建房（`room-create` 0x1E/1）和匹配（`match-start` 0x20/1）两条路径
    都要走自动切图** —— 新手向导的「团队模式」走的就是 match-start（body 是
    opaque 的，但里面塞了 pattern_id），服务端扫到已知 pattern 就切图。命中 0
    张或 ≥2 张时**不强行猜**，只把扫描结果记账。
* ⚠️ **客户端认 pattern，不认 level_id**：洛阳能进时服务端恒发 `1028`
    （`= PATTERN_BY_LEVEL[10036]`），而洛阳的 level_id 是 10036。所以切图后
    必须发 pattern（宛城 29 / 樊城 20001）。**且 room-create 的回显 与
    `instance-minimal-update` 的 `RESOURCE_ID` 必须是同一个 ID** —— 否则
    一次进图里客户端拿到两个地图 ID，照样卡在载入地图。
* ⚠️ **教学模式 ≠ 匹配模式**（`pattern_level_map.csv` 的 `mode_a`）：
    宛城 `mode_a=1`，客户端**直接** `ROOM_CREATE → ROOM_ENTER`，**不走**
    `match-start / match-result / match-enter-instance`；洛阳/樊城 `mode_a=2`
    走完整匹配链路。实机日志逐条对上，别用同一套进图方案套两者。
* ⚠️ **卡片 ID 无条件进日志**：上行是**加密**的（`frames-1.bin` 里只有密文），
    所以 `room-create-resource-id client=<客户端发的> echo=<我们回的> echoMode=… level=… pattern=… match=0/1`
    是唯一能看到「客户端到底点了哪张卡」的地方，**别删**。
  * ⚠️ **出生点 Z 差异巨大**（洛阳 0.218 / 宛城 20.46 / 樊城 43.25）。
    改图不改 Z 会出生在地下。三张图的出生点 Z 都落在各自墙高区间内
    （交叉验证：`lysd` -6.34..8.46 / `pve_gc` 17.06..36.07 / `tszz` 42.23..64.26）。
  * **未闭合**：① echo=client 是否真的能进宛城/樊城/人机（**2026-09-19 实机待验**，
    之前默认 echo=level 必卡，要等重启后看日志）；② 教学模式是否要服务端推任务列表
    （大厅文档提到走 PropSheet 引导链，如果卡在教学第一步大概率差这个）；
    ③ 樊城攻城玩法（夺旗 / 双阵营 / 占领点状态）；
    ④ ~~**爬楼梯**：`advanceGround()` 的 `delta[2]` 硬编码 `0.0`，Z 轴从没动过~~
    **已于 2026-09-19 修好**：新增 `scripts/heightfield.py` + `controls.groundZFollow()`，
    角色 Z 按 `data/scene/<场景>/heightfield.json` 跟随可站立高度。
    数据源是 **amodellist.dat（场景资产表）不是 PAMH**（PAMH 只有 33×33≈9m/格，
    且世界 XY→索引的 AABB 解不出来）。樊城实测：地面 43.25 / 走道 ≈44.4 /
    城墙顶 >55 —— **能上城墙了**。默认开启，`T7_HEIGHTFIELD=off` 可关；
    **没有 heightfield.json 的场景（lysd / pve_gc）完全不生效，逐位不变**。
    构建脚本：`t7_amodellist_extract.py` → `t7_heightfield_build.py`。
    ⚠️ 楼梯在高度场里是**垂直通道**（amodellist 没有台阶几何），靠
    `MAX_GROUND_Z_STEP = 6.0` 限速避免瞬移。
    PAMH 高度图**已于 2026-09-19 找到并解出**（在 `D://dfjq_out//map`，
    不是"丢失"——详见 SKILL）。格式：`PAMH` + `u32 层数+1` + `u32 1089(33×33)` +
    f32 数据；**层 0 = 地面**（三关实测 lysd `0..6.252` / pve_gc `-28.93..41.046` /
    tszz `41.232..43.896`，各自 spawn Z 都落在范围内），**层 1+ = 城墙/屋顶**。
    三关地面层已导出到 `server/data/scene/<场景>/aheightmap_ground.json`
    （脚本：工作区 `t7_pamh_extract.py`）。
    ⚠️ **仍差世界 XY→33×33 索引的映射（AABB）**：用每关 2 个真实出生点做正方形
    网格拟合（含 4 种翻转/转置），残差最小仍有 0.29~1.15 米 → 33×33 很可能不是
    覆盖全图而是局部 tile。**AABB 闭合前别拿它做碰撞。**
    ⚠️ 另外：**樊城「走着沉地下」不是地形起伏**（tszz 地面只差 2.7 米，很平），
    而是**城墙碰撞缺失**（该关空气墙只有 5 段、全在出生点 120 单位外）。
    要挡城墙得用 PAMH 的**层 1+**，不是层 0。

## 证据与未迁移边界

字节来源是 `tools/m3/{protocol,login_flow,room_flow,leave_flow,vision_flow}.py`
及 `tools/m3/Capture-M3LoopbackConnection.ps1` 的对应 reactive 分支、
Add-M3RoundInfo、Add-M3RoundStateInfo、UNIFY_TIME。打包时只读复制 canonical
源码到 `scripts/codec/`，不维护第二套 canonical codec。

这是 native 适配器的**合成合同验证**，不是原客户端消费、进图、相机或完整
离房验收。业务阶段 `*-sent` 只表示脚本产出，实际发出由 native journal 记录。
本地位置 `(272.451,157.634,0.218)`，敌方 `(271.046,156.857,0.218)`；武将、
武器和敌人字节对照原 PS 纯编码函数通过。攻击/伤害/死亡裁决、完整跳跃/碰撞
（空气墙**已接线但默认关闭、未实机验收**；动作期间的**地面回声压制**同样已接线、
默认关闭、未实机验收；Havok 地形碰撞 `acollisionmap.dat` 未做）、
相机自然初始化的客户端验收及反向问题仍未完成；`outcome=none`，不伪造扣血或战斗结果。
MATCH union 未恢复，仅按已有 listener 边界保留原 bytes。

STATE_VERSION 1 可无副作用升级到 2，既有 battle 不自动 prime、重建或重置位置；
完整新初始化须重新登录并进入新实例。旧 v1 不认识 ground/prime 定时器，故旧包
拒绝 v2 状态回退，完整回退需停止服务后恢复备份再启动。v2 内的热重载与回退继续
保留 pending timers 和 ground 状态。可选 `ground.lastAdvanceAt` / `nextDeadline` 保存积分与绝对节拍，
`timingTick` 随每次ground输出同步tick。缺字段或旧版输出造成标记失配时，仅重建时间锚点，
保留位置/输入/连续移动起点，不补算未知区间；首次接管可不移动，之后恢复正常积分。
可选 `ground.moveStartedAt` 保存连续移动起点；
旧 v2 活动 ground 缺少该字段时，从新脚本首次处理后续普通有效移动起计，不追认历史。
新版本热加载保留已有起点，初始化STOP不启动或清除它；离房后的新实例不继承。
旧v2缺少`groundEnabled`时，仅角色已就绪且`phase == "game-sent"`兼容开放；
显式False优先，不根据已选将/出战推测GAME。旧PREPARE/START的ground-step和prime到期均静默。
首次进入GAME清理旧准备按键、移动起点及积分时钟，取消残留ground/prime timer，保留已知位置/heading；
不补算旧位移、不自动起步，也不撤销旧客户端已经显示的动作。完整历史包往返加载尚未验证。
GAME门打开后，旧prime-start只消费；旧prime-stop在普通运动仍有效时只消费，
静止时用现有位置/heading收束STOP。首次有效按键取消旧prime pending。
已有会话热加载不补发新初始化，也不自动撤销客户端残留相机组；新入场才采用完整新顺序。
Alt按用户“好像正常”的反馈保持原生CMD_OBS自由观察行为，停止追踪；不新增selector43或服务端锁朝向。
这仅表示暂未观察到异常，不是正式协议/画面验收。
玩家 MouseSensitivity 按用户决定保留现值，
服务端不强制 VM 的 0.25、不修改客户端设置。

所有业务时间采用 context `nowMs` 的 host monotonic 毫秒；host/VM 时钟差异
及 UNIFY/倒计时消费仍须同身份客户端验证。不能把脚本 GAME 或测试通过
解释为可玩性验收，也不能将该子集冒称为当前 PS server 的全功能替代。

验证：`python -m unittest tests.test_native_business tests.test_native_business_controls tests.test_native_ground_timing tests.test_native_business_camera tests.test_native_business_parity -v`。
测试仅临时打包脚本并注入合成事件，不创建 socket、不运行 VM 或客户端。

**`scripts/` 下没有 tests 目录，上面的 unittest 目标不在本机。** 本机实际可跑的离线验证
是工作区里的七个脚本（用实机报文驱动，纯逻辑；`t7_jump_verify.py` 第 11 节与
`t7_special_keys_verify.py` 第 3 节需要读客户端 exe 的 TDR 宏表）：

```
python "C:/Users/wynkz/WorkBuddy AI/2026-09-18-22-23-32/t7_jump_verify.py"              #  71 项，跳跃链 + 落地三拍 + 客户端宏表回归
python "C:/Users/wynkz/WorkBuddy AI/2026-09-18-22-23-32/t7_battle_verify.py"            # 189 项，四向攻击/招架 + 按住松开语义 + 类型安全
python "C:/Users/wynkz/WorkBuddy AI/2026-09-18-22-23-32/t7_wasd_verify.py"              # 187 项，WASD 回退基线锁
python "C:/Users/wynkz/WorkBuddy AI/2026-09-18-22-23-32/t7_special_keys_verify.py"      # 267 项*，SHIFT 快跑 + F/E/X/冲刺 + key_comb 位图自证 + 逐拍时长 + T7_MS_* 覆盖 + **回退回归门**
python "C:/Users/wynkz/WorkBuddy AI/2026-09-18-22-23-32/t7_airwall_wire_verify.py"      #  94 项，空气墙接线（开关关闭逐位不变 + 撞墙拦下 + 真实数据端到端）
python "C:/Users/wynkz/WorkBuddy AI/2026-09-18-22-23-32/t7_level_verify.py"            # 293 项，关卡换图（默认逐位不变 + 出生点/场景/回显成套 + 报文里的坐标 + level.ini 通道 + echo=client|level|pattern A/B + 端到端 handleRoom + **room-enter 路径** + **按卡片自动定图 / 教学-匹配分流** + **match-start 路径自动切图** + **可站立高度场**）
python "C:/Users/wynkz/WorkBuddy AI/2026-09-18-22-23-32/t7_airwall.py" --selftest       #  12 项，空气墙解析与 2D 判定
```

合计 **1114 项，全绿**（`71 + 189 + 187 + 268 + 94 + 293 + 12 = 1114`，
最后那个 12 是 `t7_airwall.py --selftest`，**别漏算**）。
`*` = `t7_special_keys_verify.py` 会 `glob` 扫
`server/data/*/wire/frames-1.jsonl`，**项数随服务端 wire 会话数增长**，
所以 268 是"本机当前会话数"下的值；其余六套项数是固定的。

> 原来的第七套 `t7_battle_suppress_verify.py`（62 项）随「压制地面回声」功能一起
> **归档**到工作区 `rolledback-20260919/`，不再参与日常套件。

七套现在都打同一行 `检查项 N，失败 M`，所以合计可以**一条命令现算**，
不用再手抄数字（手抄就会烂，我就烂过一次）：

```bash
PY="C:/Users/wynkz/.workbuddy-ai/binaries/python/versions/3.13.12/python.exe"
cd "C:/Users/wynkz/WorkBuddy AI/2026-09-18-22-23-32"
for s in t7_jump_verify t7_battle_verify t7_wasd_verify t7_special_keys_verify \
         t7_airwall_wire_verify t7_level_verify; do "$PY" "$s.py" | grep 检查项; done
"$PY" t7_airwall.py --selftest | tail -1
```

`t7_jump_verify.py` / `t7_battle_verify.py` / `t7_special_keys_verify.py` /
`t7_airwall_wire_verify.py` / `t7_level_verify.py` 都含走真实
`app.handleEvent` / `app.handleRoom` / `app.validateState` 的端到端段落。

`t7_special_keys_verify.py` 的五条硬约束值得单独记住：
1. 它**全量重扫 wire 日志**，从 `battle-raw … hex=` 里取真实
   (key_comb, msg_id, body)，所以特殊键的位映射不是手写常量而是实机回放；
2. 它**反向断言**特殊键 msg_id 与四向攻击 msg_id **完全互斥**，
   同时**正向断言**「四向攻击 msg_id + F/E/X 位共置」的实机样本**确实存在**
   （`300550` + `0x0080028D`）——⚠️ 旧的「四向攻击不许带 F 位」断言**是错的**，
   已被新实机数据推翻；权威是 `msg_id`，`key_comb` 位只表示「这个键此刻按着」；
3. 它**重读 TieJiClient.exe 宏表**，把 `KEY_*` / `RES_MSG_*` / `ACT_STATE_*` /
   `E_CS_PROTO_MOVE_FAST_RUN_REQ` 全部对锁，手改常量会立刻红；
4. 它**断言逐拍定时器排定的实际 delta**（前踢 360/1333/500、冲刺 1933/500），
   所以“常量改了但下发还是老节拍”这种半吊子改动会被抓住；
5. 它**断言冲刺攻击链里不含 438**——那个状态在 `s_act_state_cli` 全表入边为 0，
   是报文不可达的，回退加回去会立刻红。
6. ⚠️ **它带"回退回归门"（§5.10 / §7.7）**：断言 `_canPreempt` / `SPECIAL_CANCEL_MODE` /
   `SPECIAL_PASS_MS` / `SPECIAL_BEAT_SRC_BY_INTENT` **都不存在**，
   并断言"蓄力中按 F 必须被拒"。谁要是把那一轮改动重新加回来，这里立刻红——
   **红的时候先读完 `rolledback-20260919/README.md` 再决定要不要绕过。**

`t7_level_verify.py` 有两条很不一样的约束，别按别的套件的套路写：
1. **每个小节都用「子进程」跑** —— `contracts.py` 在 **import 时**就把 `T7_LEVEL`
   吃成常量了，`importlib.reload` 会牵动整个 `codec` 包、还会留脏缓存。
   所以统一 `subprocess` 起一个只 dump JSON 的工人进程，环境干净。
   套件的 `probe(level, ini)` 就是这个工人，**新增断言请走它，别直接 import**；
2. **`probe` 默认带 `ini="off"`**（`T7_LEVEL_INI=off`）—— 测「纯净默认」时必须隔离，
   否则现场 `server/level.ini` 里写了宛城，「默认逐位不变」会被现场配置污染而假红。
   §11 才放开 ini 去做真实配置文件通道的断言；
3. **出生点必须真的写进下行报文** —— 实测它在 `actorVision` / `enemyVision`
   的 **offset 47**（三个连续 big-endian float32）。套件会把这个坐标从报文里
   **搜出来**比对，「常量改了但下发还是老坐标」会被抓住。

`t7_airwall_wire_verify.py` 的三条硬约束同样别绕：
1. **开关关闭时必须逐位不变**——位置写入量、reason 标签、session 形状（连
   `airwallBlocked` 这个键都不许出现）逐项对锁，防止「顺手把开关默认打开」；
2. **撞墙后位置永远不许越过墙面**——人造墙走 20 拍 + 真实 `city_temp_low` 走 20 拍，
   断言每一步的服务端位置和每一条 40 字节下行里的位置都在墙的近侧；
3. **快跑 sel=63 那条路径单独守卫**——贴墙按 SHIFT 必须只发 STOP。

> 归档的 `rolledback-20260919/t7_battle_suppress_verify.py` 里那三条硬约束
> （开关关闭逐位不变 / 压制期间位置照常推进 / 动作结束自动恢复 + 空气墙优先）
> 设计思路仍然有效，**将来若重做这个功能，直接把它移回来用**。

改完脚本记得清 `__pycache__`（服务端跑 Python 3.14，离线桩用 3.13，缓存会串）：
`rm -rf scripts/__pycache__ scripts/codec/__pycache__`。
改动**不用重启进程**，但要**新开一局**：每条新连接会给当前 `scripts/` 重算一个
`scriptVersion` 并按新代码跑（2026-09-22 实测：同一个 PID 44792 从 01:41 跑到现在，
13:45/14:55/16:12 三局的 `scriptVersion` 分别是 `80a8…`/`a95f…`/`f3eb…`，而 14:30 才加的
35 号坐骑报文出现在 15:06 那局 893 次 ⇒ 改完只要让玩家**退局重进**）。
每局会在 `data/<会话>/revisions/t7rev_<hash>/` 落一份快照，但**别拿它当判据**：
当天七局里只有 16:12 那局真的落了目录，而且那个 `t7rev_98cf…` 跟同一局的
`scriptVersion=f3eb…` **对不上**。要确认生效，去新抓包里 grep 新代码独有的 `reason`。

`native/integration_test.py`从LOAD_OK的ROUND_STATE读取实际准备时长，先验证PREPARE/START输入静默，
完整等待GAME后才测新heading及WALK→RUN。仓库包需约20分钟准备等待，桌面既有30秒配置仍须保留；
测试不修改生产倒计时。`tests.test_native_integration_phase`仅以mock验证等待/失败门，不能替代socket执行。
