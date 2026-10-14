# Linux 游戏服务部署

云端运行独立 Rust 房间与战斗服务，通过 TLS/WSS 直接接入。玩家在启动器填写 IP 或域名与端口，使用当前昵称启动，在游戏大厅创建或加入房间。无需注册或群验证；游客票据不证明昵称所有权。

## 构建与安装

在项目根目录用 `scripts/package_game_server.py --output OUTPUT.tar.gz` 打包独立源码，输出文件必须尚不存在。归档包含源码、测试、锁文件、部署模板、许可证和 SHA256SUMS，不包含游戏资源、本机配置或缓存。

使用 Rust 1.99.0、C 编译器、链接器、CMake 和 CA 证书。在带 systemd 的 Linux 上解包、验证并构建：

```sh
tar -xzf SOURCE.tar.gz
cd t7-game-server-source
sha256sum --check SHA256SUMS
cargo +1.99.0 test --locked
cargo +1.99.0 build --locked --release
```

将二进制安装到版本目录，例如 `/opt/t7-game-server/releases/RELEASE/bin/t7-game-server`，模式 755；将 `current` 链接指向所选版本。安装 `deploy/lobby-maps.json` 到 `/etc/t7-game-server/lobby-maps.json`，按所用客户端配置地图目录。

安装 [systemd 模板](t7-game-server.service)，创建系统组 `t7-tls`；将 [环境配置示例](public-service.env.example) 安装为 `/etc/t7-game-server/service.env`。监听地址、端口和房间容量由部署者填写，容量范围 1–20 人。

## TLS 证书

证书必须受客户端系统信任，并覆盖启动器填写的 IP 或域名。公网 IP 可使用 [Let's Encrypt IP 证书](https://letsencrypt.org/2026/03/11/shorter-certs-certbot.html)，有效约六天，必须配置自动续期。

Certbot 5.4+ 支持 IP 证书。部署者确认 CA 服务条款后，在端口 80 没有其他 HTTP 服务时签发；邮件地址与条款选项按部署者自己的选择提供：

```sh
/opt/t7-certbot/bin/certbot certonly --standalone --preferred-profile shortlived --ip-address PUBLIC_IP
```

创建 `/etc/t7-game-server/tls`，目录所有者 `root:t7-tls`、模式 750；密钥 `privkey.pem` 为 640，证书 `fullchain.pem` 为 644。动态服务用户通过补充组读取密钥，私钥不进入分发包。

[证书安装脚本](install-certificate.sh)先更新密钥，再发布证书。首次签发后，以 `RENEWED_LINEAGE` 指向证书目录运行脚本；续期配置仅对所选游戏证书设置此 deploy hook：

```sh
/opt/t7-certbot/bin/certbot reconfigure --cert-name CERT_NAME --deploy-hook 'sh /opt/t7-game-server/current/deploy/install-certificate.sh'
```

[续期服务](t7-certificate-renew.service)和[定时器](t7-certificate-renew.timer)每天检查两次。Standalone 验证临时监听端口 80；若已有 HTTP 服务，请使用官方文档的 webroot 方式。Rust 每分钟验证并热加载新证书，已有游戏连接继续运行。Certbot 仅用于证书运维，不承担游戏逻辑。

## 启动与验收

云防火墙允许所选游戏 TCP 端口；HTTP-01 签证还需允许 TCP 80。安装并启动服务和续期定时器：

```sh
systemctl daemon-reload
systemctl enable --now t7-game-server t7-certificate-renew.timer
systemctl status t7-game-server
journalctl -u t7-game-server -n 100 --no-pager
```

`https://HOST:PORT/ready` 应返回 `ready: true` 和 `guestAdmission: true`；`productionAdmission: false` 表示游客模式不提供正式账号鉴权。

启动器「设置 → 联机服务器」填写地址与端口，启用连接后正常启动。HTTPS `POST /auth/guest` 自动获取短期票据，原生网关通过 WSS Authorization 头一次性兑换。缺少、过期、重复票据或身份不匹配的 Hello 会被拒绝。

服务重启不恢复房间；发布新版本应先结束活动对局，再切换版本链接并重启。20 人角色映射有自动化测试，20 人实机性能尚未验证。
