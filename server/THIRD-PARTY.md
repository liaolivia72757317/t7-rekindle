# 服务端第三方依赖

服务端源码沿用仓库 MIT 许可。下表记录 Maven 发布元数据中的运行依赖许可，完整坐标固定在 `runtime-dependencies.lock`；上游 Jar 中的 LICENSE/NOTICE 随嵌套 Jar 保留。

- Spring Boot 4.0.8 BOM 管理 Spring、MySQL Connector/J、Thymeleaf、Lettuce、Micrometer 等版本；MyBatis Starter 4.0.1、Bouncy Castle 1.86 显式固定。
- Flyway 通过 `spring-boot-starter-flyway` 接入自动配置；core/mysql 固定为 11.20.3，覆盖 BOM 的 11.14.1，以支持 MySQL 8.4 而不产生旧验证版本告警。两者均为 Apache-2.0。
- MySQL Connector/J 除 GPL-2.0 外附带 Universal FOSS Exception 1.0；以 [上游完整许可](https://github.com/mysql/mysql-connector-j/blob/9.7.0/LICENSE) 为准。
- 外部 MySQL Server 8.4.11 为 GPL-2.0；Redis Server 8.6.6 在其多许可选项中选择 AGPL-3.0，见 [Redis 官方许可](https://redis.io/legal/licenses/)。两种服务不打入应用 Jar。
- 测试依赖：JUnit 6.0.3（EPL-2.0）、Testcontainers 2.0.5（MIT）、ArchUnit 1.4.2（Apache-2.0，包含 BSD 组件）、JaCoCo 0.8.14（EPL-2.0）；不进入生产 Jar。

## 管理界面资源

资源随应用 Jar 从 `/admin/assets/` 同源提供，不在运行时访问 CDN。vendor 文件不做修改；项目覆盖集中在 `ui-tokens.css`、`ui-tabler-adapter.css`、`admin.css` 与 `announcement-editor.css`。

| 资源 | 固定版本与来源 | 本地许可 |
| --- | --- | --- |
| Tabler CSS / JavaScript | [@tabler/core 1.6.1](https://github.com/tabler/tabler/releases/tag/@tabler%2Fcore@1.6.1)，来自 npm 发布包；仅保留核心构建与 source maps | [MIT](src/main/resources/static/admin/assets/vendor/tabler/1.6.1/LICENSE) |
| 内含 Bootstrap / Popper | Bootstrap 5.3.8（Tabler 源码内置）/ Popper 2.11.8，不重复加载其他 bundle | [Bootstrap MIT](src/main/resources/static/admin/assets/vendor/tabler/1.6.1/LICENSE.bootstrap) · [Popper MIT](src/main/resources/static/admin/assets/vendor/tabler/1.6.1/LICENSE.popper) |
| Tabler Icons 线性 SVG | [3.49.0](https://github.com/tabler/tabler-icons/releases/tag/v3.49.0)，从 npm `@tabler/icons` 的 `icons/outline/` 按需合并为 `icons.svg`，保留原始路径与 24×24 画布 | [MIT](src/main/resources/static/admin/assets/vendor/tabler-icons/3.49.0/LICENSE) |
| EasyMDE CSS / JavaScript | [2.21.0](https://github.com/Ionaru/easy-markdown-editor/releases/tag/2.21.0)，来自 npm `easymde` 同版本包的 `dist/`，不改写 bundle | [MIT](src/main/resources/static/admin/assets/vendor/easymde/2.21.0/LICENSE) |
| EasyMDE 内含组件 | 按上游 2.21.0 的 `package-lock.json`：CodeMirror 5.65.15、Marked 4.1.0、codemirror-spell-checker 1.1.2、Typo.js 1.2.2；不重复加载，拼写检查关闭，预览采用项目内与启动器一致的渲染逻辑 | [CodeMirror MIT](src/main/resources/static/admin/assets/vendor/easymde/2.21.0/LICENSE.codemirror) · [Marked MIT 等声明](src/main/resources/static/admin/assets/vendor/easymde/2.21.0/LICENSE.marked) · [拼写检查 MIT](src/main/resources/static/admin/assets/vendor/easymde/2.21.0/LICENSE.codemirror-spell-checker) · [Typo.js BSD-3-Clause](src/main/resources/static/admin/assets/vendor/easymde/2.21.0/LICENSE.typo-js) |

`brand.ico` 复用仓库内启动器的既有品牌图标，不属于 Tabler 资源。升级组件库后须回归[公共组件验收页](../docs/planning/admin-ui-checklist.md)。

## 运行依赖

| Maven artifact | Version | 发布许可元数据 |
| --- | --- | --- |
| `ch.qos.logback:logback-classic` | 1.5.38 | [EPL-2.0](https://www.eclipse.org/legal/epl-v20.html) / [LGPL-2.1-only](https://www.gnu.org/licenses/old-licenses/lgpl-2.1.html) |
| `ch.qos.logback:logback-core` | 1.5.38 | [EPL-2.0](https://www.eclipse.org/legal/epl-v20.html) / [LGPL-2.1-only](https://www.gnu.org/licenses/old-licenses/lgpl-2.1.html) |
| `com.fasterxml.jackson.core:jackson-annotations` | 2.21 | [The Apache Software License, Version 2.0](https://www.apache.org/licenses/LICENSE-2.0.txt) |
| `com.fasterxml.jackson.core:jackson-core` | 2.21.5 | [The Apache Software License, Version 2.0](https://www.apache.org/licenses/LICENSE-2.0.txt) |
| `com.fasterxml.jackson.core:jackson-databind` | 2.21.5 | [The Apache Software License, Version 2.0](https://www.apache.org/licenses/LICENSE-2.0.txt) |
| `com.mysql:mysql-connector-j` | 9.7.0 | The GNU General Public License, v2 with Universal FOSS Exception, v1.0 |
| `com.zaxxer:HikariCP` | 7.0.2 | [The Apache Software License, Version 2.0](https://www.apache.org/licenses/LICENSE-2.0.txt) |
| `commons-logging:commons-logging` | 1.3.6 | [Apache-2.0](https://www.apache.org/licenses/LICENSE-2.0.txt) |
| `io.lettuce:lettuce-core` | 6.8.2.RELEASE | [MIT](https://github.com/redis/lettuce/blob/main/LICENSE) |
| `io.micrometer:micrometer-commons` | 1.16.7 | [The Apache Software License, Version 2.0](http://www.apache.org/licenses/LICENSE-2.0.txt) |
| `io.micrometer:micrometer-core` | 1.16.7 | [The Apache Software License, Version 2.0](http://www.apache.org/licenses/LICENSE-2.0.txt) |
| `io.micrometer:micrometer-jakarta9` | 1.16.7 | [The Apache Software License, Version 2.0](http://www.apache.org/licenses/LICENSE-2.0.txt) |
| `io.micrometer:micrometer-observation` | 1.16.7 | [The Apache Software License, Version 2.0](http://www.apache.org/licenses/LICENSE-2.0.txt) |
| `io.netty:netty-buffer` | 4.2.17.Final | [Apache License, Version 2.0](https://www.apache.org/licenses/LICENSE-2.0) |
| `io.netty:netty-codec-base` | 4.2.17.Final | [Apache License, Version 2.0](https://www.apache.org/licenses/LICENSE-2.0) |
| `io.netty:netty-codec-dns` | 4.2.17.Final | [Apache License, Version 2.0](https://www.apache.org/licenses/LICENSE-2.0) |
| `io.netty:netty-common` | 4.2.17.Final | [Apache License, Version 2.0](https://www.apache.org/licenses/LICENSE-2.0) |
| `io.netty:netty-handler` | 4.2.17.Final | [Apache License, Version 2.0](https://www.apache.org/licenses/LICENSE-2.0) |
| `io.netty:netty-resolver` | 4.2.17.Final | [Apache License, Version 2.0](https://www.apache.org/licenses/LICENSE-2.0) |
| `io.netty:netty-resolver-dns` | 4.2.17.Final | [Apache License, Version 2.0](https://www.apache.org/licenses/LICENSE-2.0) |
| `io.netty:netty-transport` | 4.2.17.Final | [Apache License, Version 2.0](https://www.apache.org/licenses/LICENSE-2.0) |
| `io.netty:netty-transport-native-unix-common` | 4.2.17.Final | [Apache License, Version 2.0](https://www.apache.org/licenses/LICENSE-2.0) |
| `io.projectreactor:reactor-core` | 3.8.7 | [Apache License, Version 2.0](https://www.apache.org/licenses/LICENSE-2.0.txt) |
| `jakarta.activation:jakarta.activation-api` | 2.1.4 | [EDL 1.0](http://www.eclipse.org/org/documents/edl-v10.php) |
| `jakarta.annotation:jakarta.annotation-api` | 3.0.0 | [EPL 2.0](https://www.eclipse.org/legal/epl-2.0) / [GPL2 w/ CPE](https://www.gnu.org/software/classpath/license.html) |
| `jakarta.xml.bind:jakarta.xml.bind-api` | 4.0.5 | [Eclipse Distribution License - v 1.0](http://www.eclipse.org/org/documents/edl-v10.php) |
| `org.apache.logging.log4j:log4j-api` | 2.25.5 | [Apache-2.0](https://www.apache.org/licenses/LICENSE-2.0.txt) |
| `org.apache.logging.log4j:log4j-to-slf4j` | 2.25.5 | [Apache-2.0](https://www.apache.org/licenses/LICENSE-2.0.txt) |
| `org.apache.tomcat.embed:tomcat-embed-core` | 11.0.24 | [Apache License, Version 2.0](http://www.apache.org/licenses/LICENSE-2.0.txt) |
| `org.apache.tomcat.embed:tomcat-embed-el` | 11.0.24 | [Apache License, Version 2.0](http://www.apache.org/licenses/LICENSE-2.0.txt) |
| `org.apache.tomcat.embed:tomcat-embed-websocket` | 11.0.24 | [Apache License, Version 2.0](http://www.apache.org/licenses/LICENSE-2.0.txt) |
| `org.attoparser:attoparser` | 2.0.7.RELEASE | [The Apache Software License, Version 2.0](https://www.apache.org/licenses/LICENSE-2.0.txt) |
| `org.bouncycastle:bcprov-jdk18on` | 1.86 | [Bouncy Castle Licence](https://www.bouncycastle.org/licence.html) |
| `org.flywaydb:flyway-core` | 11.20.3 | [Apache License, Version 2.0](https://github.com/flyway/flyway/blob/main/README.txt) |
| `org.flywaydb:flyway-mysql` | 11.20.3 | [Apache License, Version 2.0](https://github.com/flyway/flyway/blob/main/README.txt) |
| `org.hdrhistogram:HdrHistogram` | 2.2.2 | [Public Domain, per Creative Commons CC0](http://creativecommons.org/publicdomain/zero/1.0/) / [BSD-2-Clause](https://opensource.org/licenses/BSD-2-Clause) |
| `org.jspecify:jspecify` | 1.0.1 | [The Apache License, Version 2.0](http://www.apache.org/licenses/LICENSE-2.0.txt) |
| `org.latencyutils:LatencyUtils` | 2.0.3 | [Public Domain, per Creative Commons CC0](http://creativecommons.org/publicdomain/zero/1.0/) |
| `org.mybatis.spring.boot:mybatis-spring-boot-autoconfigure` | 4.0.1 | [The Apache Software License, Version 2.0](https://www.apache.org/licenses/LICENSE-2.0.txt) |
| `org.mybatis.spring.boot:mybatis-spring-boot-starter` | 4.0.1 | [The Apache Software License, Version 2.0](https://www.apache.org/licenses/LICENSE-2.0.txt) |
| `org.mybatis:mybatis` | 3.5.19 | [The Apache Software License, Version 2.0](https://www.apache.org/licenses/LICENSE-2.0.txt) |
| `org.mybatis:mybatis-spring` | 4.0.0 | [The Apache Software License, Version 2.0](https://www.apache.org/licenses/LICENSE-2.0.txt) |
| `org.reactivestreams:reactive-streams` | 1.0.4 | [MIT-0](https://spdx.org/licenses/MIT-0.html) |
| `org.slf4j:jul-to-slf4j` | 2.0.18 | [MIT](https://opensource.org/license/mit) |
| `org.slf4j:slf4j-api` | 2.0.18 | [MIT](https://opensource.org/license/mit) |
| `org.springframework.boot:spring-boot` | 4.0.8 | [Apache License, Version 2.0](https://www.apache.org/licenses/LICENSE-2.0) |
| `org.springframework.boot:spring-boot-actuator` | 4.0.8 | [Apache License, Version 2.0](https://www.apache.org/licenses/LICENSE-2.0) |
| `org.springframework.boot:spring-boot-actuator-autoconfigure` | 4.0.8 | [Apache License, Version 2.0](https://www.apache.org/licenses/LICENSE-2.0) |
| `org.springframework.boot:spring-boot-autoconfigure` | 4.0.8 | [Apache License, Version 2.0](https://www.apache.org/licenses/LICENSE-2.0) |
| `org.springframework.boot:spring-boot-data-commons` | 4.0.8 | [Apache License, Version 2.0](https://www.apache.org/licenses/LICENSE-2.0) |
| `org.springframework.boot:spring-boot-data-redis` | 4.0.8 | [Apache License, Version 2.0](https://www.apache.org/licenses/LICENSE-2.0) |
| `org.springframework.boot:spring-boot-flyway` | 4.0.8 | [Apache License, Version 2.0](https://www.apache.org/licenses/LICENSE-2.0) |
| `org.springframework.boot:spring-boot-health` | 4.0.8 | [Apache License, Version 2.0](https://www.apache.org/licenses/LICENSE-2.0) |
| `org.springframework.boot:spring-boot-http-converter` | 4.0.8 | [Apache License, Version 2.0](https://www.apache.org/licenses/LICENSE-2.0) |
| `org.springframework.boot:spring-boot-jackson` | 4.0.8 | [Apache License, Version 2.0](https://www.apache.org/licenses/LICENSE-2.0) |
| `org.springframework.boot:spring-boot-jdbc` | 4.0.8 | [Apache License, Version 2.0](https://www.apache.org/licenses/LICENSE-2.0) |
| `org.springframework.boot:spring-boot-micrometer-metrics` | 4.0.8 | [Apache License, Version 2.0](https://www.apache.org/licenses/LICENSE-2.0) |
| `org.springframework.boot:spring-boot-micrometer-observation` | 4.0.8 | [Apache License, Version 2.0](https://www.apache.org/licenses/LICENSE-2.0) |
| `org.springframework.boot:spring-boot-netty` | 4.0.8 | [Apache License, Version 2.0](https://www.apache.org/licenses/LICENSE-2.0) |
| `org.springframework.boot:spring-boot-persistence` | 4.0.8 | [Apache License, Version 2.0](https://www.apache.org/licenses/LICENSE-2.0) |
| `org.springframework.boot:spring-boot-security` | 4.0.8 | [Apache License, Version 2.0](https://www.apache.org/licenses/LICENSE-2.0) |
| `org.springframework.boot:spring-boot-servlet` | 4.0.8 | [Apache License, Version 2.0](https://www.apache.org/licenses/LICENSE-2.0) |
| `org.springframework.boot:spring-boot-sql` | 4.0.8 | [Apache License, Version 2.0](https://www.apache.org/licenses/LICENSE-2.0) |
| `org.springframework.boot:spring-boot-starter` | 4.0.8 | [Apache License, Version 2.0](https://www.apache.org/licenses/LICENSE-2.0) |
| `org.springframework.boot:spring-boot-starter-actuator` | 4.0.8 | [Apache License, Version 2.0](https://www.apache.org/licenses/LICENSE-2.0) |
| `org.springframework.boot:spring-boot-starter-data-redis` | 4.0.8 | [Apache License, Version 2.0](https://www.apache.org/licenses/LICENSE-2.0) |
| `org.springframework.boot:spring-boot-starter-flyway` | 4.0.8 | [Apache License, Version 2.0](https://www.apache.org/licenses/LICENSE-2.0) |
| `org.springframework.boot:spring-boot-starter-jackson` | 4.0.8 | [Apache License, Version 2.0](https://www.apache.org/licenses/LICENSE-2.0) |
| `org.springframework.boot:spring-boot-starter-jdbc` | 4.0.8 | [Apache License, Version 2.0](https://www.apache.org/licenses/LICENSE-2.0) |
| `org.springframework.boot:spring-boot-starter-logging` | 4.0.8 | [Apache License, Version 2.0](https://www.apache.org/licenses/LICENSE-2.0) |
| `org.springframework.boot:spring-boot-starter-micrometer-metrics` | 4.0.8 | [Apache License, Version 2.0](https://www.apache.org/licenses/LICENSE-2.0) |
| `org.springframework.boot:spring-boot-starter-security` | 4.0.8 | [Apache License, Version 2.0](https://www.apache.org/licenses/LICENSE-2.0) |
| `org.springframework.boot:spring-boot-starter-thymeleaf` | 4.0.8 | [Apache License, Version 2.0](https://www.apache.org/licenses/LICENSE-2.0) |
| `org.springframework.boot:spring-boot-starter-tomcat` | 4.0.8 | [Apache License, Version 2.0](https://www.apache.org/licenses/LICENSE-2.0) |
| `org.springframework.boot:spring-boot-starter-tomcat-runtime` | 4.0.8 | [Apache License, Version 2.0](https://www.apache.org/licenses/LICENSE-2.0) |
| `org.springframework.boot:spring-boot-starter-webmvc` | 4.0.8 | [Apache License, Version 2.0](https://www.apache.org/licenses/LICENSE-2.0) |
| `org.springframework.boot:spring-boot-thymeleaf` | 4.0.8 | [Apache License, Version 2.0](https://www.apache.org/licenses/LICENSE-2.0) |
| `org.springframework.boot:spring-boot-tomcat` | 4.0.8 | [Apache License, Version 2.0](https://www.apache.org/licenses/LICENSE-2.0) |
| `org.springframework.boot:spring-boot-transaction` | 4.0.8 | [Apache License, Version 2.0](https://www.apache.org/licenses/LICENSE-2.0) |
| `org.springframework.boot:spring-boot-web-server` | 4.0.8 | [Apache License, Version 2.0](https://www.apache.org/licenses/LICENSE-2.0) |
| `org.springframework.boot:spring-boot-webmvc` | 4.0.8 | [Apache License, Version 2.0](https://www.apache.org/licenses/LICENSE-2.0) |
| `org.springframework.data:spring-data-commons` | 4.0.7 | [Apache License, Version 2.0](https://www.apache.org/licenses/LICENSE-2.0) |
| `org.springframework.data:spring-data-keyvalue` | 4.0.7 | [Apache License, Version 2.0](https://www.apache.org/licenses/LICENSE-2.0) |
| `org.springframework.data:spring-data-redis` | 4.0.7 | [Apache License, Version 2.0](https://www.apache.org/licenses/LICENSE-2.0) |
| `org.springframework.security:spring-security-config` | 7.0.7 | [Apache License, Version 2.0](https://www.apache.org/licenses/LICENSE-2.0) |
| `org.springframework.security:spring-security-core` | 7.0.7 | [Apache License, Version 2.0](https://www.apache.org/licenses/LICENSE-2.0) |
| `org.springframework.security:spring-security-crypto` | 7.0.7 | [Apache License, Version 2.0](https://www.apache.org/licenses/LICENSE-2.0) |
| `org.springframework.security:spring-security-web` | 7.0.7 | [Apache License, Version 2.0](https://www.apache.org/licenses/LICENSE-2.0) |
| `org.springframework:spring-aop` | 7.0.9 | [Apache License, Version 2.0](https://www.apache.org/licenses/LICENSE-2.0) |
| `org.springframework:spring-beans` | 7.0.9 | [Apache License, Version 2.0](https://www.apache.org/licenses/LICENSE-2.0) |
| `org.springframework:spring-context` | 7.0.9 | [Apache License, Version 2.0](https://www.apache.org/licenses/LICENSE-2.0) |
| `org.springframework:spring-context-support` | 7.0.9 | [Apache License, Version 2.0](https://www.apache.org/licenses/LICENSE-2.0) |
| `org.springframework:spring-core` | 7.0.9 | [Apache License, Version 2.0](https://www.apache.org/licenses/LICENSE-2.0) |
| `org.springframework:spring-expression` | 7.0.9 | [Apache License, Version 2.0](https://www.apache.org/licenses/LICENSE-2.0) |
| `org.springframework:spring-jdbc` | 7.0.9 | [Apache License, Version 2.0](https://www.apache.org/licenses/LICENSE-2.0) |
| `org.springframework:spring-oxm` | 7.0.9 | [Apache License, Version 2.0](https://www.apache.org/licenses/LICENSE-2.0) |
| `org.springframework:spring-tx` | 7.0.9 | [Apache License, Version 2.0](https://www.apache.org/licenses/LICENSE-2.0) |
| `org.springframework:spring-web` | 7.0.9 | [Apache License, Version 2.0](https://www.apache.org/licenses/LICENSE-2.0) |
| `org.springframework:spring-webmvc` | 7.0.9 | [Apache License, Version 2.0](https://www.apache.org/licenses/LICENSE-2.0) |
| `org.thymeleaf:thymeleaf` | 3.1.5.RELEASE | [The Apache Software License, Version 2.0](https://www.apache.org/licenses/LICENSE-2.0.txt) |
| `org.thymeleaf:thymeleaf-spring6` | 3.1.5.RELEASE | [The Apache Software License, Version 2.0](https://www.apache.org/licenses/LICENSE-2.0.txt) |
| `org.unbescape:unbescape` | 1.1.6.RELEASE | [The Apache Software License, Version 2.0](http://www.apache.org/licenses/LICENSE-2.0.txt) |
| `org.yaml:snakeyaml` | 2.5 | [Apache License, Version 2.0](http://www.apache.org/licenses/LICENSE-2.0.txt) |
| `redis.clients.authentication:redis-authx-core` | 0.1.1-beta2 | [MIT](https://github.com/redis/redis-authx-core/blob/master/LICENSE) |
| `tools.jackson.core:jackson-core` | 3.1.5 | [The Apache Software License, Version 2.0](https://www.apache.org/licenses/LICENSE-2.0.txt) |
| `tools.jackson.core:jackson-databind` | 3.1.5 | [The Apache Software License, Version 2.0](https://www.apache.org/licenses/LICENSE-2.0.txt) |

## 更新清单

在 `server/` 执行 `mvn -B -ntp dependency:list -DincludeScope=runtime -DoutputFile=target/runtime-dependencies.txt`，核对解析版本和每个上游 POM/Jar 的许可，再更新本文件与锁定清单。不要复制本地仓库路径、私有镜像地址或构建日志。
