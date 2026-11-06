# tools/mock.py
"""模拟工具：把设置里填写的文本原样交给 agent。

存在的意义是验证「工具参数链路」本身：注册表里声明一个字段 → 设置面板出现输入框
→ 保存后下一轮对话里工具就能读到。新增同类工具只需要写 Python，
前端不需要任何改动。
"""

from __future__ import annotations

from langchain.tools import tool

from tools.errors import tool_error


def build_mock_tools(config: dict) -> list:
    """按注入的配置构造 ``mock_lookup``。"""
    text = str((config or {}).get("MOCK_TEXT") or "")

    @tool
    def mock_lookup(query: str = "") -> str:
        """读取「模拟数据」工具里配置的文本内容。

        Args:
            query: 可选的说明或关键词，只用于回显，不影响返回内容。

        Returns:
            配置的文本；尚未配置时返回以 "Error:" 开头的说明。
        """
        if not text.strip():
            return tool_error(
                "missing_config",
                "「模拟数据」还没有填写内容。",
                hint="在 设置 → 工具 → 模拟数据 的「内容」里填一段文字，保存后重试。",
            )
        body = f"模拟数据：{text}"
        keyword = (query or "").strip()
        if keyword:
            body += f"\n请求参数：{keyword}"
        return body

    return [mock_lookup]
