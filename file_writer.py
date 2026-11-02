"""أداة حفظ التقارير — محصنة ضد Path Traversal، كتابة غير حاجبة."""

import asyncio
import os
import re
from pathlib import Path

from tools.base import BaseTool
from tools.models import FileWriteInput, FileWriteOutput

OUTPUT_DIR = Path(os.getenv("REPORTS_DIR", "./reports")).resolve()


class FileWriterTool(BaseTool):
    name = "file_writer"
    description = "يحفظ نصًا (تقريرًا) في ملف Markdown داخل مجلد التقارير الآمن."
    input_schema = FileWriteInput
    output_schema = FileWriteOutput

    async def run(self, params: FileWriteInput) -> FileWriteOutput:
        try:
            safe_name = re.sub(r"[^a-zA-Z0-9_؀-ۿ\-]", "_",
                               params.filename)
            if not safe_name.endswith(".md"):
                safe_name += ".md"

            OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
            full_path = (OUTPUT_DIR / safe_name).resolve()

            # تأكيد أمني: الملف داخل مجلد التقارير فقط
            if not str(full_path).startswith(str(OUTPUT_DIR)):
                return FileWriteOutput(success=False, error_message="مسار غير مسموح")

            await asyncio.to_thread(full_path.write_text, params.content, "utf-8")
            return FileWriteOutput(
                success=True, path=str(full_path),
                bytes_written=len(params.content.encode("utf-8")),
            )
        except Exception as exc:
            return FileWriteOutput(success=False,
                                   error_message=f"{type(exc).__name__}: {exc}")
