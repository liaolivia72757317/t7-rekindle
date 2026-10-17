# Python 测试对照依赖

此目录保存以下 PyPI wheel 的原始内容，不修改上游源码。Python 对照测试与隔离原生测试宿主从此目录加载，不依赖系统安装的 Python 包。产品运行时使用 Rust，不分发这些依赖。

| 包 | 版本 | 用途 | 许可证 |
| --- | --- | --- | --- |
| [py_trees](https://github.com/splintered-reality/py_trees) | 2.6.0 | 电脑玩家行为树 | BSD-3-Clause |
| [pydot](https://github.com/pydot/pydot) | 4.0.1 | py_trees 的声明依赖 | MIT，含 Python-2.0 授权代码 |
| [pyparsing](https://github.com/pyparsing/pyparsing) | 3.3.3 | pydot 的声明依赖 | MIT |

版本固定在 `requirements.txt`；原始许可文本位于各包的 `.dist-info/licenses/`，文件清单见对应 `METADATA` 中的 `License-File`。这里只运行行为树决策，不调用图形渲染程序。

更新时从 PyPI 下载固定版本的 wheel，核对依赖与许可，替换对应包及元数据，并运行业务测试和隔离原生主机测试。
