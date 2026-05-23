"""Tests for the iPhone-photo failure modes of `core/document_converter.py`.

Two regressions led to this file:

1. HEIC files (iPhone default since iOS 11) were rejected by extension
   with a generic "Unsupported format" — the user has no way to learn
   that switching their phone to JPEG would fix it. We now route HEIC
   through `_convert_image` and surface a `DependencyMissingError` with
   an explicit fix path when `pillow-heif` is not installed server-side.

2. `MAX_FILE_SIZE_BYTES` was 10 MB — too small for Live Photos /
   48-MP shots / 4K HDR captures (15-25 MB range). Bumped to 25 MB.
"""

from __future__ import annotations

import pytest

from agent_orchestrator.core.document_converter import (
    MAX_FILE_SIZE_BYTES,
    DependencyMissingError,
    DocumentConverter,
    UnsupportedFormatError,
)


def test_heic_is_in_supported_types():
    """The smoking gun: HEIC / HEIF must be recognised at the dispatch
    layer so the request reaches `_convert_image` instead of bouncing
    with `UnsupportedFormatError`."""
    assert ".heic" in DocumentConverter.SUPPORTED_TYPES
    assert ".heif" in DocumentConverter.SUPPORTED_TYPES
    assert DocumentConverter.SUPPORTED_TYPES[".heic"] == "_convert_image"
    assert DocumentConverter.SUPPORTED_TYPES[".heif"] == "_convert_image"


def test_max_file_size_covers_typical_iphone_photo():
    """A 4K iPhone photo is 3-8 MB; Live Photos / 48 MP reach 15-25 MB.
    The cap must comfortably exceed a single Live Photo (~20 MB)."""
    assert MAX_FILE_SIZE_BYTES >= 20 * 1024 * 1024


@pytest.mark.asyncio
async def test_heic_without_pillow_heif_raises_dependency_missing(tmp_path, monkeypatch):
    """When `pillow-heif` is not installed, the user must see a
    `DependencyMissingError` with actionable text (which dep to install
    OR how to change iPhone settings) — NOT a bare ImportError or a
    generic 500."""
    fake_heic = tmp_path / "IMG_0001.heic"
    fake_heic.write_bytes(b"\x00\x00\x00\x18ftypheic")  # plausible HEIC magic

    # Force the import to fail even if pillow-heif happens to be on the
    # test environment's PATH.
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *a, **kw):
        if name == "pillow_heif":
            raise ImportError("simulated missing pillow-heif")
        return real_import(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    converter = DocumentConverter(output_dir=str(tmp_path))
    with pytest.raises(DependencyMissingError) as exc_info:
        await converter.convert(str(fake_heic))
    msg = str(exc_info.value)
    # Either guidance the user can act on — the dep name OR the iPhone setting.
    assert ("pillow-heif" in msg) or ("Most Compatible" in msg)


@pytest.mark.asyncio
async def test_non_heic_still_unsupported(tmp_path):
    """Sanity: random extensions still raise `UnsupportedFormatError` —
    the HEIC change must not have weakened the registry."""
    fake = tmp_path / "weird.xyz"
    fake.write_bytes(b"???")
    with pytest.raises(UnsupportedFormatError):
        await DocumentConverter(output_dir=str(tmp_path)).convert(str(fake))
