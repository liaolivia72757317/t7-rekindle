# 项目文档

从[项目首页](../README.md)了解项目目标和当前进展，再按需要选择下面的入口。

## 阅读入口

| 你要做什么 | 阅读文档 |
| --- | --- |
| 使用启动器，确认运行环境和客户端兼容性 | [产品需求与使用说明](requirements.md) |
| 排查目录、名称、启动和退出问题 | [使用说明中的问题排查](requirements.md#问题排查与反馈) |
| 从源码构建、测试或制作运行包 | [开发与交付](development.md) |
| 为社区项目命名，区分原游戏和兼容标识 | [项目命名规范](development.md#项目命名规范) |
| 了解 UI 1.1 的接入、状态绑定和界面验收 | [UI 1.1 实施与验收](planning/ui-handoff-v1.1.md) |
| 修改服务端管理页面，检查公共组件与交互状态 | [管理 UI 规范](planning/admin-ui.md) · [验收表](planning/admin-ui-checklist.md) |
| 理解模块职责、进程关系和启动流程 | [架构说明](architecture.md) |
| 了解四实体职责、离线与在线后端、Rust 双平台部署及战斗权威边界 | [目标架构](planning/target-architecture.md) |
| 了解原版客户端无 TP 启动的验证结果与实施前置 | [TP 纯内存启动方案](planning/tp-memory-launch.md) |
| 查找源码、测试、构建产物或内置文档 | [目录与源码范围](source-layout.md) |
| 修改原生接口、生命周期、日志或设置格式 | [运行契约](runtime-contracts.md) |
| 构建无游戏进程的战斗宿主，了解帧传输接口 | [战斗运行时与独立宿主](battle-server.md) |
| 接入 C#／Rust 准入契约，导出和预检离线战斗资源 | [战斗公共契约与资源数据包](battle-contracts.md) |
| 接入离线单机、LAN 与 Java 调度的独立战斗节点 | [开源战斗服三模式实施计划](planning/battle-network-v1.md) |
| 将战局规则迁入 Rust，了解宿主边界与迁移验收 | [Rust 战斗运行时迁移](planning/rust-runtime-migration.md) |
| 配置先锋阵容、武器，了解进场与复活同步 | [先锋阵容与武器配置](loadouts.md) |
| 查看称号奖励规则、管理入口和游戏内同步方式 | [账户称号](account-titles.md) |
| 理解近战攻击、格挡、挡反、硬直与破防规则 | [近战攻防规则](combat-rules.md) |
| 排查普通格挡接触缺失，核实各武器的防御几何来源 | [防御几何来源核实](planning/guard-geometry-investigation.md) |
| 规划多武将、多武器、个人作战、团队协同与战场指挥 | [电脑玩家 AI 升级规划](planning/computer-player-ai-upgrade.md) |
| 规划主动采集上传、双方向训练、评测审批与模型演进 | [电脑玩家双方向训练与模型演进](planning/computer-player-ai-training.md) |
| 查阅默认装备阶段的电脑 AI 规则、伪代码与优化任务 | [电脑玩家 AI 优化交接](planning/computer-player-ai-handoff.md) |
| 了解人机对战和局域网联机的推进顺序 | [实施与验收计划](planning/implementation-plan.md) |
| 选择可以参与的工作，查看剩余验收事项 | [任务清单](planning/task-list.md) |

## 文档分工

- 使用说明描述当前用户可见的行为和限制；架构、运行契约描述当前实现。
- 目标架构定义实体职责、模式路由、部署与公共契约边界，不表示目标能力已经交付。
- 实施计划定义阶段目标和验收条件；任务清单记录已有基础与待完成事项。
- “已有实现”表示代码已存在，“自动化覆盖”表示仓库有相应用例；某次构建是否通过，以该提交的测试结果为准。
- 真实客户端验收单独记录。合成报告中的 `realClient=false` 表示未启动真实客户端，不代表完整游戏流程可玩。

## 参与与许可

贡献前阅读[开发规范](../AGENTS.md)。一般问题通过本仓库 Issue 反馈，安全问题按[安全说明](../SECURITY.md)提交。

项目源码采用 [MIT License](../LICENSE)，分发依赖见[第三方依赖说明](../THIRD-PARTY.txt)。原游戏客户端及资源由用户自行准备，不随项目分发。
