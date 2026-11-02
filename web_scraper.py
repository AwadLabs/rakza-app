"""أداة قراءة الصفحات: httpx async + trafilatura (مع سقوط إلى BeautifulSoup)."""

import asyncio

import httpx

from tools.base import BaseTool
from tools.models import ScrapeInput, ScrapeOutput

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; RakzaBot/1.0)"}


class WebScraperTool(BaseTool):
    name = "web_scraper"
    description = "يقرأ صفحة ويب ويستخرج النص النظيف (حد أقصى 20000 حرف)."
    input_schema = ScrapeInput
    output_schema = ScrapeOutput

    def __init__(self, timeout: float = 15.0):
        self._timeout = timeout

    async def run(self, params: ScrapeInput) -> ScrapeOutput:
        url = params.url.strip()
        if not url.startswith(("http://", "https://")):
            return ScrapeOutput(success=False, url=url, error_message="URL غير صالح")

        try:
            async with httpx.AsyncClient(
                timeout=self._timeout, follow_redirects=True,
                headers=HEADERS, max_redirects=5,
            ) as client:
                resp = await client.get(url)
                resp.raise_for_status()
        except httpx.TimeoutException:
            return ScrapeOutput(success=False, url=url,
                                error_message="انتهت مهلة الاتصال (Timeout)")
        except httpx.HTTPStatusError as exc:
            return ScrapeOutput(success=False, url=url,
                                error_message=f"HTTP {exc.response.status_code}")
        except Exception as exc:
            return ScrapeOutput(success=False, url=url,
                                error_message=f"{type(exc).__name__}: {exc}")

        title, text = await asyncio.to_thread(self._extract, resp.text)
        if not text or len(text) < 100:
            return ScrapeOutput(
                success=False, url=url, title=title,
                error_message="لم يُستخرج نص كافٍ (الصفحة قد تكون JS-rendered)",
            )

        text = text[: params.max_chars]
        return ScrapeOutput(success=True, url=url, title=title,
                            content=text, char_count=len(text))

    @staticmethod
    def _extract(html: str) -> tuple[str, str]:
        """trafilatura أولًا (الأدق)، وBeautifulSoup احتياطيًا."""
        try:
            import trafilatura

            text = trafilatura.extract(html, include_comments=False,
                                       include_tables=True) or ""
            if text:
                from bs4 import BeautifulSoup

                soup = BeautifulSoup(html, "html.parser")
                title = (soup.title.string.strip()
                         if soup.title and soup.title.string else "")
                return title, text.strip()
        except ImportError:
            pass

        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "html.parser")
        title = (soup.title.string.strip()
                 if soup.title and soup.title.string else "")
        for tag in soup(["script", "style", "nav", "footer",
                         "header", "aside", "form", "noscript"]):
            tag.decompose()
        return title, " ".join(soup.get_text(separator=" ").split())
