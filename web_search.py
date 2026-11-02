"""أداة البحث: Tavily ← Serper ← DuckDuckGo — سقوط تلقائي عند فشل كل مزود."""

import asyncio
import os

import httpx

from tools.base import BaseTool
from tools.models import SearchInput, SearchOutput, SearchResultItem


class WebSearchTool(BaseTool):
    name = "web_search"
    description = "يبحث في الويب ويعيد عناوين وروابط ومقتطفات."
    input_schema = SearchInput
    output_schema = SearchOutput

    def __init__(self, timeout: float = 20.0):
        self._timeout = timeout

    async def run(self, params: SearchInput) -> SearchOutput:
        """يجرّب المزودين بالترتيب ويجمع أخطاءهم — آخر فشل هو الرسالة المعادة."""
        if not params.query.strip():
            return SearchOutput(success=False, error_message="استعلام فارغ")

        errors: list[str] = []
        for provider in self._available_providers():
            items, err = await self._try(provider, params)
            if items:
                return SearchOutput(success=True, provider=provider, results=items)
            errors.append(f"[{provider}] {err}")
        return SearchOutput(
            success=False,
            error_message=" | ".join(errors) or "لا مزود بحث متاحًا",
        )

    @staticmethod
    def _available_providers() -> list[str]:
        """السقوط مبني على المفاتيح المتوفرة فعلًا — لا افتراضات."""
        chain = []
        if os.getenv("TAVILY_API_KEY"):
            chain.append("tavily")
        if os.getenv("SERPER_API_KEY"):
            chain.append("serper")
        chain.append("duckduckgo")  # مجاني دائمًا كملاذ أخير
        return chain

    async def _try(self, provider: str, p: SearchInput):
        try:
            if provider == "tavily":
                return await self._tavily(p), None
            if provider == "serper":
                return await self._serper(p), None
            return await self._duckduckgo(p), None
        except Exception as exc:
            return [], f"{type(exc).__name__}: {exc}"

    async def _tavily(self, p: SearchInput) -> list[SearchResultItem]:
        from tavily import AsyncTavilyClient

        client = AsyncTavilyClient(api_key=os.getenv("TAVILY_API_KEY"))
        resp = await asyncio.wait_for(
            client.search(query=p.query, max_results=p.max_results),
            timeout=self._timeout,
        )
        return [
            SearchResultItem(title=r.get("title", ""), url=r.get("url", ""),
                             snippet=r.get("content", "")[:500])
            for r in resp.get("results", [])
        ]

    async def _serper(self, p: SearchInput) -> list[SearchResultItem]:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(
                "https://google.serper.dev/search",
                headers={"X-API-KEY": os.getenv("SERPER_API_KEY", "")},
                json={"q": p.query, "num": p.max_results},
            )
            resp.raise_for_status()
        return [
            SearchResultItem(title=r.get("title", ""), url=r.get("link", ""),
                             snippet=r.get("snippet", "")[:500])
            for r in resp.json().get("organic", [])
        ]

    async def _duckduckgo(self, p: SearchInput) -> list[SearchResultItem]:
        """مكتبة ddgs أولًا، وعند فشلها نقطة HTML الرسمية عبر httpx."""
        try:
            return await self._ddgs_lib(p)
        except Exception:
            return await self._ddg_html(p)

    async def _ddgs_lib(self, p: SearchInput) -> list[SearchResultItem]:
        try:
            from ddgs import DDGS                  # الاسم الجديد للحزمة
        except ImportError:
            from duckduckgo_search import DDGS

        def _sync() -> list[dict]:                # مكتبة متزامنة ← خيط منفصل
            with DDGS() as ddgs:
                return list(ddgs.text(p.query, max_results=p.max_results))

        raw = await asyncio.wait_for(asyncio.to_thread(_sync), timeout=self._timeout)
        return [
            SearchResultItem(title=r.get("title", ""), url=r.get("href", ""),
                             snippet=r.get("body", "")[:500])
            for r in raw
        ]

    async def _ddg_html(self, p: SearchInput) -> list[SearchResultItem]:
        """احتياطي بلا اعتماديات إضافية: صفحة نتائج DuckDuckGo HTML."""
        from urllib.parse import parse_qs, urlparse

        from bs4 import BeautifulSoup

        async with httpx.AsyncClient(
            timeout=self._timeout, follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (compatible; RakzaBot/1.0)"},
        ) as client:
            resp = await client.post("https://html.duckduckgo.com/html/",
                                     data={"q": p.query})
            resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "html.parser")
        items: list[SearchResultItem] = []
        for res in soup.select(".result")[: p.max_results]:
            a = res.select_one(".result__a")
            sn = res.select_one(".result__snippet")
            if not a or not a.get("href"):
                continue
            url = a["href"]
            if "uddg=" in url:                     # روابط DDG مغلفة بتحويل
                url = parse_qs(urlparse(url).query).get("uddg", [url])[0]
            items.append(SearchResultItem(
                title=a.get_text(strip=True), url=url,
                snippet=(sn.get_text(strip=True) if sn else "")[:500]))
        return items
