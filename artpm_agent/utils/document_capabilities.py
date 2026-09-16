"""Lightweight document capability declarations shared by UI and adapters."""

MINERU_SUPPORTED_SUFFIXES = frozenset(
    {
        ".bmp",
        ".docx",
        ".gif",
        ".jp2",
        ".jpeg",
        ".jpg",
        ".pdf",
        ".png",
        ".pptx",
        ".tif",
        ".tiff",
        ".webp",
        ".xlsx",
    }
)

__all__ = ["MINERU_SUPPORTED_SUFFIXES"]
