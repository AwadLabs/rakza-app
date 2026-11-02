"""محرك «ركزة» — منسّق الوكلاء الشجري (Async بالكامل).

المعمارية: مشرف (مستوى 1) يخطّط ← باحث (مستوى 2) يجمع عبر أدوات الويب
← محلل (مستوى 2) يبني تقرير SWOT ويحفظه عبر file_writer.

السبورة هنا In-Process (قاموس مشترك داخل العملية) — كافية لنشر العقدة
الواحدة. عند التوسع متعدد العمال، استبدلها بـ StateManager (Redis +
PostgreSQL) من الإطار الأم دون تغيير واجهة الوكلاء.

كل استدعاء أداة يمر عبر ToolGateway المقيد بصلاحيات YAML.
"""

from __future__ import annotations

import json
import os
import re
from typing import AsyncGenerator
from urllib.parse import urlparse

from core.models import AgentEvent, AnalysisResult, ResearchPlan
from tools.registry import ToolGateway, build_gateway

# مؤشرات لغوية لاستخلاص SWOT من النصوص المجموعة (وضع بلا LLM)
WEAKNESS_MARKERS = ["complaint", "expensive", "slow", "late", "problem",
                    "issue", "شكوى", "غالٍ", "غالي", "بطيء", "تأخر", "مشكلة"]
STRENGTH_MARKERS = ["best", "fast", "quality", "fresh", "popular", "leading",
                    "الأفضل", "سريع", "جودة", "طازج", "رائد", "مميز"]


class RakzaEngine:
    """المحرك الرئيسي — يبث أحداث الوكلاء ويعيد التقرير النهائي."""

    def __init__(self, plugin_path: str = "plugins/business-plugin.yaml"):
        self.gateway: ToolGateway = build_gateway(plugin_path)
        self.blackboard: dict[str, object] = {}   # سبورة In-Process
        self._llm = self._build_llm()

    # ---------- نقطة الدخول: بث الأحداث ----------

    async def analyze(self, goal: str) -> AsyncGenerator[dict, None]:
        """مولّد async — كل حدث dict جاهز للتحويل إلى SSE."""
        try:
            # ===== المستوى 1: المشرف يخطّط =====
            yield self._ev("supervisor_agent", 1, "RUNNING",
                           "المشرف يحلّل الطلب ويبني خطة البحث…")
            plan = await self._plan(goal)
            self.blackboard["plan"] = plan.model_dump()
            yield self._ev("supervisor_agent", 1, "INFO",
                           f"الهدف: «{plan.company}» — {len(plan.queries)} استعلامات بحث")
            yield self._ev("supervisor_agent", 1, "DONE",
                           "وزّع المهام: باحث ← جمع البيانات، محلل ← تقرير SWOT")

            # ===== المستوى 2: الباحث يجمع (أحداثه تُبث لحظيًا) =====
            raw: list[dict] = []
            sources: list[str] = []
            gaps: list[str] = []
            async for item in self._research(plan):
                if item["event"] == "_research_data":
                    raw, sources, gaps = item["data"]
                else:
                    yield item
            self.blackboard["competitor_raw_data"] = {
                "raw": raw, "sources": sources}
            if not raw:
                yield self._ev("researcher_agent", 2, "FAILED",
                               "لم يُجمع أي محتوى — كل المصادر فشلت")
            else:
                yield self._ev("researcher_agent", 2, "DONE",
                               f"اكتمل الجمع: {len(raw)} وثيقة، {len(sources)} مصدرًا")

            # ===== المستوى 2: المحلل يكتب ويحفظ =====
            yield self._ev("analyst_agent", 2, "RUNNING",
                           "المحلل يقرأ competitor_raw_data ويبني تقرير SWOT…")
            report = await self._build_report(plan, raw, sources, gaps)
            self.blackboard["final_report"] = report

            save = await self.gateway.call(
                "analyst_agent", "file_writer",
                {"filename": f"rakza_{plan.company}", "content": report})
            report_path = save.get("path") if save.get("success") else None
            if save.get("success"):
                yield self._ev("analyst_agent", 2, "DONE",
                               f"حُفظ التقرير: {os.path.basename(report_path)}")
            else:
                yield self._ev("analyst_agent", 2, "FAILED",
                               f"فشل حفظ الملف: {save.get('error_message')}")

            result = AnalysisResult(
                company=plan.company, report_markdown=report,
                report_path=report_path, sources=sources, gaps=gaps,
                llm_used=self._llm is not None)
            yield {"event": "result", "data": result.model_dump()}

        except Exception as exc:  # خطأ غير متوقع في المنسّق نفسه
            yield self._ev("system", 0, "FAILED",
                           f"خطأ غير متوقع: {type(exc).__name__}: {exc}")

    # ---------- المشرف ----------

    async def _plan(self, goal: str) -> ResearchPlan:
        """تخطيط عبر LLM إن وُجد، وإلا استخلاص حتمي للشركة والاستعلامات."""
        if self._llm is not None:
            try:
                resp = await self._llm.chat.completions.create(
                    model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
                    response_format={"type": "json_object"},
                    messages=[{"role": "system", "content": (
                        "Extract the target company/product and build 3-4 web "
                        "search queries to analyze its competitors. Return JSON: "
                        '{"company": str, "queries": [str]}')},
                        {"role": "user", "content": goal}],
                )
                data = json.loads(resp.choices[0].message.content)
                return ResearchPlan.model_validate(data)
            except Exception:
                pass  # سقوط صامت إلى الوضع الحتمي

        company = self._extract_company(goal)
        queries = [
            f"{company} المنافسون",
            f"{company} competitors alternatives",
            f"{company} pricing features",
            f"{company} reviews complaints",
        ]
        return ResearchPlan(company=company, queries=queries)

    @staticmethod
    def _extract_company(goal: str) -> str:
        """يأخذ ما بين «» أو أول كلمتين — بلا تخمين مبتكر."""
        m = re.search(r"[«\"']([^»\"']{2,60})[»\"']", goal)
        if m:
            return m.group(1).strip()
        words = goal.split()
        return " ".join(words[:2]) if words else "الهدف"

    # ---------- الباحث ----------

    async def _research(self, plan: ResearchPlan) -> AsyncGenerator[dict, None]:
        """لكل استعلام: بحث ثم قراءة أعلى نتيجتين. الفشل يوثَّق كفجوة.

        مولّد async: يبث أحداثه فور حدوثها، ويختم بعنصر _research_data
        يحمل (raw, sources, gaps) ليلتقطه المنسّق.
        """
        raw: list[dict] = []
        sources: list[str] = []
        gaps: list[str] = []

        for q in plan.queries:
            yield self._ev("researcher_agent", 2, "RUNNING", f"بحث: {q}")
            search = await self.gateway.call(
                "researcher_agent", "web_search",
                {"query": q, "max_results": 5})
            if not search.get("success"):
                gaps.append(f"فشل البحث «{q}»: {search.get('error_message')}")
                yield self._ev("researcher_agent", 2, "FAILED", f"فشل البحث: {q}")
                continue

            for item in search.get("results", [])[:2]:
                url = item.get("url", "")
                if not url:
                    continue
                scrape = await self.gateway.call(
                    "researcher_agent", "web_scraper",
                    {"url": url, "max_chars": 5000})
                if scrape.get("success"):
                    raw.append({"title": item.get("title") or scrape.get("title") or url,
                                "url": url, "snippet": item.get("snippet", ""),
                                "content": scrape.get("content", "")})
                    sources.append(url)
                else:
                    # نحتفظ بالمقتطف على الأقل ونوثّق فشل القراءة
                    raw.append({"title": item.get("title", url), "url": url,
                                "snippet": item.get("snippet", ""), "content": ""})
                    sources.append(url)
                    gaps.append(f"تعذّرت قراءة {urlparse(url).netloc}: "
                                f"{scrape.get('error_message')}")

        # الحمولة الختامية يلتقطها المنسّق ولا تُبث للواجهة
        yield {"event": "_research_data", "data": (raw, sources, gaps)}

    # ---------- المحلل ----------

    async def _build_report(self, plan: ResearchPlan, raw: list[dict],
                            sources: list[str], gaps: list[str]) -> str:
        if self._llm is not None and raw:
            try:
                corpus = "\n\n".join(
                    f"### {d['title']}\n{d['url']}\n{d['content'] or d['snippet']}"
                    for d in raw)[:24000]
                resp = await self._llm.chat.completions.create(
                    model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
                    messages=[{"role": "system", "content": (
                        "أنت محلل منافسين. اكتب تقرير Markdown عربيًا بالهيكل: "
                        "## نظرة عامة على السوق، ## جدول مقارنة المنافسين، "
                        "## تحليل SWOT، ## توصيات استراتيجية، ## فجوات البيانات. "
                        "كل ادعاء مرتبط بالمصادر أدناه فقط — ممنوع الاختلاق.")},
                        {"role": "user",
                         "content": f"الهدف: {plan.company}\n\nالبيانات:\n{corpus}"}],
                )
                return resp.choices[0].message.content
            except Exception:
                pass  # سقوط إلى القالب الحتمي

        return self._template_report(plan, raw, sources, gaps)

    def _template_report(self, plan: ResearchPlan, raw: list[dict],
                         sources: list[str], gaps: list[str]) -> str:
        """تقرير حتمي من البيانات المجموعة — بلا LLM وبلا اختلاق."""
        corpus_text = " ".join(d["content"] or d["snippet"] for d in raw).lower()
        weaknesses = [m for m in WEAKNESS_MARKERS if m in corpus_text]
        strengths = [m for m in STRENGTH_MARKERS if m in corpus_text]

        rows = "\n".join(
            f"| {d['title'][:40]} | {urlparse(d['url']).netloc} | "
            f"{(d['snippet'] or '—')[:80]} |" for d in raw[:8])

        return f"""# تقرير ركزة: تحليل منافسي «{plan.company}»

## نظرة عامة على السوق
جُمعت {len(raw)} وثيقة من {len(sources)} مصدرًا حول «{plan.company}» ومنافسيه.
هذا التقرير وُلّد في الوضع الحتمي (بدون نموذج لغوي) — فعّل OPENAI_API_KEY لصياغة أعمق.

## جدول مقارنة المنافسين
| المصدر | النطاق | أبرز ما ورد |
|---|---|---|
{rows or '| — | — | لم تُجمع بيانات |'}

## تحليل SWOT
**نقاط القوة:** {'، '.join(strengths) if strengths else 'لم تُرصد مؤشرات قوة صريحة في النصوص المجموعة.'}

**نقاط الضعف:** {'، '.join(weaknesses) if weaknesses else 'لم تُرصد شكاوى أو نقاط ضعف صريحة.'}

**الفرص:** معالجة نقاط الضعف المرصودة لدى المنافسين أعلاه.

**التهديدات:** حضور المنافسين المذكورين في جدول المقارنة.

## توصيات استراتيجية
1. ركّز الرسائل التسويقية على الفجوات المرصودة لدى المنافسين.
2. قارن تسعيرك بالمصادر المذكورة في الجدول.
3. أعد التحليل دوريًا لرصد التغيرات.

## فجوات البيانات
{chr(10).join('- ' + g for g in gaps) if gaps else '- لا فجوات مسجلة.'}

## المصادر
{chr(10).join('- ' + s for s in sources) if sources else '- لا مصادر.'}
"""

    # ---------- أدوات مساعدة ----------

    @staticmethod
    def _ev(agent: str, level: int, state: str, message: str) -> dict:
        return {"event": "agent",
                "data": AgentEvent(agent=agent, level=level,
                                   state=state, message=message).model_dump()}

    @staticmethod
    def _build_llm():
        """عميل OpenAI async إن وُجد المفتاح — وإلا None (وضع حتمي)."""
        if not os.getenv("OPENAI_API_KEY"):
            return None
        try:
            from openai import AsyncOpenAI
            return AsyncOpenAI()
        except ImportError:
            return None
