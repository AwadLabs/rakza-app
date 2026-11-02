"""السجل المركزي + بوابة الاستدعاء الموحدة.

الصلاحيات تُقرأ حصرًا من plugins/business-plugin.yaml — لا صلاحيات
مثبتة في الكود. البوابة تعمل In-Process الآن، وتُستبدل بعميل MCP
لاحقًا دون لمس أي وكيل.
"""

from typing import Any

import yaml

from tools.base import BaseTool
from tools.file_writer import FileWriterTool
from tools.web_scraper import WebScraperTool
from tools.web_search import WebSearchTool


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"أداة مكررة: {tool.name}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> BaseTool | None:
        return self._tools.get(name)

    def list_all(self) -> list[BaseTool]:
        return list(self._tools.values())


class ToolGateway:
    """بوابة الاستدعاء المحروسة: صلاحية ← وجود ← تحقق مخطط ← تنفيذ."""

    def __init__(self, registry: ToolRegistry, plugin: dict):
        self._registry = registry
        self._roles = {r["role_id"]: r for r in plugin.get("agent_roles", [])}

    async def call(self, role_id: str, tool_name: str, args: dict) -> dict[str, Any]:
        role = self._roles.get(role_id)
        allowed = (role or {}).get("mcp_tool_allowlist", [])
        if tool_name not in allowed:
            return {"success": False,
                    "error_message": f"الوكيل '{role_id}' لا يملك صلاحية '{tool_name}'"}

        # تطبيق حدود الدور من YAML (مثل max_results) فوق مدخلات الوكيل
        limits = ((role or {}).get("limits") or {}).get(tool_name) or {}
        args = {**args, **limits}

        tool = self._registry.get(tool_name)
        if tool is None:
            return {"success": False, "error_message": f"أداة غير مسجلة: {tool_name}"}
        try:
            params = tool.input_schema(**args)      # تحقق Pydantic الصارم
            result = await tool.run(params)
            return result.model_dump()
        except Exception as exc:                    # خطأ مخطط المدخلات مثلًا
            return {"success": False,
                    "error_message": f"{type(exc).__name__}: {exc}"}


def load_plugin(path: str = "plugins/business-plugin.yaml") -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def build_gateway(plugin_path: str = "plugins/business-plugin.yaml") -> ToolGateway:
    """مصنع التهيئة — يسجّل الأدوات ويبني البوابة من YAML."""
    registry = ToolRegistry()
    registry.register(WebSearchTool())
    registry.register(WebScraperTool())
    registry.register(FileWriterTool())
    return ToolGateway(registry, load_plugin(plugin_path))
