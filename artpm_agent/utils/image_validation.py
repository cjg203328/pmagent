"""Shared validation for local images sent to vision-capable models."""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path


MAX_IMAGE_FILE_SIZE = 15 * 1024 * 1024

_IMAGE_TYPES = {
    ".png": ("PNG", "image/png"),
    ".jpg": ("JPEG", "image/jpeg"),
    ".jpeg": ("JPEG", "image/jpeg"),
    ".webp": ("WEBP", "image/webp"),
}


@dataclass(frozen=True)
class ValidatedImage:
    """Validated image bytes and provider-safe metadata."""

    data: bytes
    media_type: str
    image_format: str
    width: int
    height: int


def load_validated_image(
    image_path: str | Path,
    *,
    max_size: int = MAX_IMAGE_FILE_SIZE,
) -> ValidatedImage:
    """Load an image after validating path, size, content, and extension."""
    path = Path(image_path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"图片不存在: {image_path}")

    expected = _IMAGE_TYPES.get(path.suffix.lower())
    if expected is None:
        raise ValueError(f"不支持的图片格式: {path.suffix or '无扩展名'}")

    file_size = path.stat().st_size
    if file_size <= 0:
        raise ValueError("图片文件不能为空")
    if file_size > max_size:
        raise ValueError(f"单张图片不能超过 {max_size // (1024 * 1024)} MB")

    data = path.read_bytes()
    if not data:
        raise ValueError("图片文件不能为空")
    if len(data) > max_size:
        raise ValueError(f"单张图片不能超过 {max_size // (1024 * 1024)} MB")

    from PIL import Image, UnidentifiedImageError

    try:
        with Image.open(BytesIO(data)) as image:
            image_format = str(image.format or "").upper()
            width, height = image.size
            image.verify()
    except (UnidentifiedImageError, OSError, SyntaxError) as error:
        raise ValueError("图片内容无法识别或已损坏") from error

    expected_format, media_type = expected
    if image_format != expected_format:
        raise ValueError(
            f"图片内容格式 {image_format or '未知'} 与扩展名"
            f" {path.suffix.lower()} 不一致"
        )

    return ValidatedImage(
        data=data,
        media_type=media_type,
        image_format=image_format,
        width=width,
        height=height,
    )
