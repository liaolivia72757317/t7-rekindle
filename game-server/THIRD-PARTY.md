# Rust 游戏服务依赖

依赖版本和校验和见 [Cargo.lock](Cargo.lock)。主要直接依赖的许可如下，使用前已核对锁定版本的 Cargo 元数据：

| 依赖 | 许可 | 用途 |
| --- | --- | --- |
| axum-server | MIT | Rustls TLS 接入与证书热加载 |
| axum | MIT | HTTP 和 WebSocket 接入 |
| tokio | MIT | 异步运行时、网络、消息通道和定时器 |
| serde / serde_json | MIT OR Apache-2.0 | 类型化 JSON 协议 |
| tracing / tracing-subscriber | MIT | 结构化运行日志 |
| uuid | Apache-2.0 OR MIT | 房间、连接和比赛 ID |
| futures-util | MIT OR Apache-2.0 | 测试客户端流与发送接口 |
| tokio-tungstenite | MIT | WebSocket 集成测试 |

传递依赖及平台相关依赖由 Cargo.lock 记录；完整元数据可通过 `cargo metadata --locked --format-version 1` 查看。此表是许可索引，不替代各依赖随源码提供的 LICENSE 或 NOTICE。分发独立服务二进制时需汇总对应目标平台的许可与声明。

本工程不分发原版客户端或游戏资源。
