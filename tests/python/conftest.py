import os

# 让 vendored 上游参考套件（tests/python）运行在「契约档」：对齐上游合成骨架契约，
# 证明本 fork 没有回归上游契约。两套口径的分界见 contracts.py 顶部的 CONTRACT_MODE 说明。
#
# ⚠️ 实机部署（t7-rekindle 宿主 / 自建 T7.Server.exe）永不置这两个环境变量，
#    默认 = 实机 / 本 fork 真实行为（客户端权威移动的镜像帧 / 控制解锁帧 / 动态单人
#    武将 / epoch 时间戳 / active=1 全部生效 —— 即 WASD / 冲刺 / 控制解锁的实机修复）。
#
# 这两个值只在「导入 contracts 之前」生效（contracts 在模块加载时读一次环境变量），
# 而 conftest 先于所有测试模块被 pytest 加载，时机正确。
os.environ["T7_CONTRACT_MODE"] = "1"
os.environ["T7_GAME_MS"] = "1200000"
