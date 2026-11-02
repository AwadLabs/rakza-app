"""خادم «ركزة» — FastAPI: بث SSE للتحليل المباشر + التقارير + الـ PWA.

التشغيل:  uvicorn api.server:app --host 0.0.0.0 --port 8000
"""

import json
import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from core.engine import RakzaEngine

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent
PWA_DIR = BASE_DIR / "pwa"
REPORTS_DIR = Path(os.getenv("REPORTS_DIR", BASE_DIR / "reports")).resolve()
REPORTS_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="RAKZA — محلل المنافسين الذكي")
app.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_methods=["*"], allow_headers=["*"])

engine = RakzaEngine(str(BASE_DIR / "plugins" / "business-plugin.yaml"))


@app.get("/api/health")
async def health() -> dict:
    return {"status": "ok", "service": "rakza",
            "llm": engine._llm is not None}


@app.get("/api/analyze")
async def analyze(goal: str = Query(..., min_length=3, max_length=500)):
    """يبث أحداث الوكلاء عبر SSE ثم يرسل النتيجة النهائية."""

    async def stream():
        async for item in engine.analyze(goal):
            yield f"event: {item['event']}\ndata: {json.dumps(item['data'], ensure_ascii=False)}\n\n"
        yield "event: close\ndata: {}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


@app.get("/api/reports")
async def list_reports() -> list[dict]:
    """أرشيف التقارير المحفوظة — الأحدث أولًا."""
    files = sorted(REPORTS_DIR.glob("*.md"),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    return [{"name": p.name, "modified": p.stat().st_mtime,
             "size": p.stat().st_size} for p in files]


@app.get("/api/reports/{name}")
async def get_report(name: str):
    """قراءة تقرير محفوظ — محمي ضد الخروج من مجلد التقارير."""
    target = (REPORTS_DIR / name).resolve()
    if REPORTS_DIR not in target.parents or target.suffix != ".md" \
            or not target.exists():
        raise HTTPException(status_code=404, detail="تقرير غير موجود")
    return FileResponse(target, media_type="text/markdown; charset=utf-8")


# الـ PWA تُقدَّم من الجذر — تُركَّب أخيرًا حتى لا تبتلع مسارات /api
app.mount("/", StaticFiles(directory=PWA_DIR, html=True), name="pwa")
