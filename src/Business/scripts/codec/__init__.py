"""T7 协议编解码工具包（离线、只读的静态分析 + 报文编解码）。

⚠️ 2026-09-20 补：本文件原本是 **0 字节**，而 ``cli.py:12`` 写的是
``from . import __version__`` ⇒ ``python -m codec`` 直接 ImportError，
整个 TDR 命令行工具（discover / enum / macro / struct / union / extract-tdr）
一个都用不了。这里把 ``__version__`` 补上，只影响 ``cli.py`` 输出 JSON 里的
``tool.version`` 字段，不参与任何编解码逻辑。

不想走 CLI 时也可以直接用函数：

    from scripts.codec import tdr
    blocks = tdr.find_tdr_blocks(open(exe, "rb").read(), source=exe)
    st = tdr.parse_tdr_struct(blocks[0], struct_name="CS_PROTO_VISION_CC_DYNAMIC_INFO")

⚠️ ``sh_proto_cs`` 在 **``TieJiClient.exe``**（23 MB）里，
**不在** ``TieJiClientBase.dll``（后者只有 ``tss_qos_pkg``）。
"""

__version__ = "0.1.0"
