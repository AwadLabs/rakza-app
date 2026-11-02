"""عقود الإدخال/الإخراج لكل أداة — التحقق الصارم يمنع تمرير بيانات خاطئة."""

from typing import Optional

from pydantic import BaseModel, Field


# ---------- البحث ----------
class SearchInput(BaseModel):
    query: str = Field(..., description="نص البحث")
    max_results: int = Field(5, ge=1, le=10, description="عدد النتائج")


class SearchResultItem(BaseModel):
    title: str
    url: str
    snippet: str


class SearchOutput(BaseModel):
    success: bool
    error_message: Optional[str] = None
    provider: str = ""
    results: list[SearchResultItem] = Field(default_factory=list)


# ---------- قراءة الصفحات ----------
class ScrapeInput(BaseModel):
    url: str
    max_chars: int = Field(5000, ge=500, le=20000)


class ScrapeOutput(BaseModel):
    success: bool
    error_message: Optional[str] = None
    url: str
    title: Optional[str] = None
    content: str = ""
    char_count: int = 0


# ---------- كتابة الملفات ----------
class FileWriteInput(BaseModel):
    filename: str = Field(..., description="اسم الملف (بلا مسار)")
    content: str


class FileWriteOutput(BaseModel):
    success: bool
    error_message: Optional[str] = None
    path: Optional[str] = None
    bytes_written: int = 0
