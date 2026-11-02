"""الكلاس الأساسي لكل أداة — async إلزاميًا لأن الشجرة تعمل بالتوازي.

قاعدة ذهبية: run() لا ترفع استثناءً أبدًا — الفشل يعود منظمًا في المخرجات.
"""

from abc import ABC, abstractmethod
from typing import Type

from pydantic import BaseModel


class BaseTool(ABC):
    name: str
    description: str
    input_schema: Type[BaseModel]
    output_schema: Type[BaseModel]

    @abstractmethod
    async def run(self, params: BaseModel) -> BaseModel:
        """تُنفذ الأداة وتعيد نموذج الإخراج. ممنوع رفع الاستثناءات."""
        ...

    def to_mcp_schema(self) -> dict:
        """يحوّل الأداة إلى صيغة MCP Tool الرسمية (للتسجيل في خادم MCP)."""
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema.model_json_schema(),
        }
