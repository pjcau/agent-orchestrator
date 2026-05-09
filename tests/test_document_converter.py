"""Tests for document_converter module.

Covers:
- CSV conversion to markdown table
- Text file conversion
- Unsupported format raises UnsupportedFormatError
- Missing dependency gives helpful error message
- File size limit enforcement
- Content limit enforcement (rows)
- convert_bytes API
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from agent_orchestrator.core.document_converter import (
    ContentLimitError,
    ConvertedDocument,
    DependencyMissingError,
    DocumentConversionError,
    DocumentConverter,
    FileTooLargeError,
    MAX_FILE_SIZE_BYTES,
    MAX_SPREADSHEET_ROWS,
    UnsupportedFormatError,
)


@pytest.fixture
def converter(tmp_path: Path) -> DocumentConverter:
    """Create a DocumentConverter with output in a temp directory."""
    return DocumentConverter(output_dir=str(tmp_path))


@pytest.fixture
def tmp_csv(tmp_path: Path) -> Path:
    """Create a simple CSV file."""
    csv_file = tmp_path / "data.csv"
    csv_file.write_text("Name,Age,City\nAlice,30,Paris\nBob,25,London\n", encoding="utf-8")
    return csv_file


@pytest.fixture
def tmp_txt(tmp_path: Path) -> Path:
    """Create a simple text file."""
    txt_file = tmp_path / "notes.txt"
    txt_file.write_text("Hello, this is a test document.\nSecond line.", encoding="utf-8")
    return txt_file


# ---------------------------------------------------------------------------
# CSV conversion
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_csv_conversion_produces_markdown_table(converter: DocumentConverter, tmp_csv: Path):
    """CSV files should be converted to a markdown table with headers."""
    result = await converter.convert(str(tmp_csv))

    assert isinstance(result, ConvertedDocument)
    assert result.file_type == "csv"
    assert result.row_count == 3  # header + 2 data rows

    # Check markdown table structure
    assert "| Name | Age | City |" in result.markdown_content
    assert "| --- | --- | --- |" in result.markdown_content
    assert "| Alice | 30 | Paris |" in result.markdown_content
    assert "| Bob | 25 | London |" in result.markdown_content


@pytest.mark.asyncio
async def test_csv_empty_file(converter: DocumentConverter, tmp_path: Path):
    """Empty CSV should produce a placeholder message."""
    empty_csv = tmp_path / "empty.csv"
    empty_csv.write_text("", encoding="utf-8")

    result = await converter.convert(str(empty_csv))
    assert result.markdown_content == "*Empty CSV file*"
    assert result.row_count == 0


@pytest.mark.asyncio
async def test_csv_row_limit(converter: DocumentConverter, tmp_path: Path):
    """CSV exceeding MAX_SPREADSHEET_ROWS should raise ContentLimitError."""
    big_csv = tmp_path / "big.csv"
    lines = ["col1,col2"] + [f"val{i},val{i}" for i in range(MAX_SPREADSHEET_ROWS + 1)]
    big_csv.write_text("\n".join(lines), encoding="utf-8")

    with pytest.raises(ContentLimitError, match="rows"):
        await converter.convert(str(big_csv))


# ---------------------------------------------------------------------------
# Text file conversion
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_text_conversion(converter: DocumentConverter, tmp_txt: Path):
    """Text files should be returned as-is in markdown."""
    result = await converter.convert(str(tmp_txt))

    assert isinstance(result, ConvertedDocument)
    assert result.file_type == "txt"
    assert "Hello, this is a test document." in result.markdown_content
    assert "Second line." in result.markdown_content


@pytest.mark.asyncio
async def test_text_empty_file(converter: DocumentConverter, tmp_path: Path):
    """Empty text file should produce a placeholder message."""
    empty_txt = tmp_path / "empty.txt"
    empty_txt.write_text("", encoding="utf-8")

    result = await converter.convert(str(empty_txt))
    assert result.markdown_content == "*Empty text file*"


# ---------------------------------------------------------------------------
# Unsupported format
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unsupported_format_raises_error(converter: DocumentConverter, tmp_path: Path):
    """Unsupported extensions should raise UnsupportedFormatError."""
    # Use a clearly-unsupported extension. Image extensions (.png, .jpg, …)
    # are now supported via OCR — see test_image_ocr_* below.
    bad_file = tmp_path / "archive.zip"
    bad_file.write_bytes(b"PK\x03\x04")

    with pytest.raises(UnsupportedFormatError, match="Unsupported file format '.zip'"):
        await converter.convert(str(bad_file))


@pytest.mark.asyncio
async def test_unsupported_format_lists_supported(converter: DocumentConverter, tmp_path: Path):
    """Error message should list supported formats."""
    bad_file = tmp_path / "data.bin"
    bad_file.write_bytes(b"\x00\x01")

    with pytest.raises(UnsupportedFormatError, match=".csv"):
        await converter.convert(str(bad_file))


# ---------------------------------------------------------------------------
# Missing dependency
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_missing_pdf_dependency(converter: DocumentConverter, tmp_path: Path):
    """PDF conversion without pymupdf should raise DependencyMissingError."""
    pdf_file = tmp_path / "test.pdf"
    pdf_file.write_bytes(b"%PDF-1.4 fake content")

    with patch.dict("sys.modules", {"fitz": None}):
        with pytest.raises(DependencyMissingError, match="pymupdf"):
            await converter.convert(str(pdf_file))


@pytest.mark.asyncio
async def test_missing_excel_dependency(converter: DocumentConverter, tmp_path: Path):
    """Excel conversion without openpyxl should raise DependencyMissingError."""
    xlsx_file = tmp_path / "test.xlsx"
    xlsx_file.write_bytes(b"PK fake xlsx")

    with patch.dict("sys.modules", {"openpyxl": None}):
        with pytest.raises(DependencyMissingError, match="openpyxl"):
            await converter.convert(str(xlsx_file))


@pytest.mark.asyncio
async def test_missing_word_dependency(converter: DocumentConverter, tmp_path: Path):
    """Word conversion without python-docx should raise DependencyMissingError."""
    docx_file = tmp_path / "test.docx"
    docx_file.write_bytes(b"PK fake docx")

    with patch.dict("sys.modules", {"docx": None}):
        with pytest.raises(DependencyMissingError, match="python-docx"):
            await converter.convert(str(docx_file))


@pytest.mark.asyncio
async def test_missing_pptx_dependency(converter: DocumentConverter, tmp_path: Path):
    """PowerPoint conversion without python-pptx should raise DependencyMissingError."""
    pptx_file = tmp_path / "test.pptx"
    pptx_file.write_bytes(b"PK fake pptx")

    with patch.dict("sys.modules", {"pptx": None}):
        with pytest.raises(DependencyMissingError, match="python-pptx"):
            await converter.convert(str(pptx_file))


@pytest.mark.asyncio
async def test_missing_html_dependency(converter: DocumentConverter, tmp_path: Path):
    """HTML conversion without markdownify should raise DependencyMissingError."""
    html_file = tmp_path / "page.html"
    html_file.write_text("<h1>Hello</h1>", encoding="utf-8")

    with patch.dict("sys.modules", {"markdownify": None}):
        with pytest.raises(DependencyMissingError, match="markdownify"):
            await converter.convert(str(html_file))


@pytest.mark.asyncio
async def test_dependency_error_suggests_install(converter: DocumentConverter, tmp_path: Path):
    """Dependency errors should suggest the pip install command."""
    pdf_file = tmp_path / "test.pdf"
    pdf_file.write_bytes(b"%PDF-1.4 fake content")

    with patch.dict("sys.modules", {"fitz": None}):
        with pytest.raises(DependencyMissingError, match="pip install"):
            await converter.convert(str(pdf_file))


# ---------------------------------------------------------------------------
# File size limit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_file_size_limit(converter: DocumentConverter, tmp_path: Path):
    """Files exceeding MAX_FILE_SIZE_BYTES should raise FileTooLargeError."""
    big_file = tmp_path / "huge.txt"
    # Create a file just over the limit (write sparse to avoid memory issues)
    with open(big_file, "wb") as f:
        f.seek(MAX_FILE_SIZE_BYTES + 1)
        f.write(b"\x00")

    with pytest.raises(FileTooLargeError, match="exceeds maximum"):
        await converter.convert(str(big_file))


@pytest.mark.asyncio
async def test_file_size_limit_bytes_api(converter: DocumentConverter):
    """convert_bytes should also enforce file size limit."""
    data = b"x" * (MAX_FILE_SIZE_BYTES + 1)

    with pytest.raises(FileTooLargeError, match="exceeds maximum"):
        await converter.convert_bytes(data, "big.txt")


# ---------------------------------------------------------------------------
# File not found
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_file_not_found(converter: DocumentConverter):
    """Non-existent file should raise DocumentConversionError."""
    with pytest.raises(DocumentConversionError, match="not found"):
        await converter.convert("/nonexistent/path/file.csv")


# ---------------------------------------------------------------------------
# convert_bytes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_convert_bytes_csv(tmp_path: Path):
    """convert_bytes should work with in-memory CSV data."""
    converter = DocumentConverter(output_dir=str(tmp_path))
    data = b"x,y\n1,2\n3,4\n"

    result = await converter.convert_bytes(data, "data.csv", save_dir=str(tmp_path))

    assert result.file_type == "csv"
    assert "| x | y |" in result.markdown_content
    assert "| 1 | 2 |" in result.markdown_content


@pytest.mark.asyncio
async def test_convert_bytes_cleans_up_temp(tmp_path: Path):
    """convert_bytes should clean up the temporary file after conversion."""
    converter = DocumentConverter(output_dir=str(tmp_path))
    data = b"hello world"

    await converter.convert_bytes(data, "temp.txt", save_dir=str(tmp_path))

    # The temp source file should be cleaned up (md file remains)
    assert not (tmp_path / "temp.txt").exists()
    assert (tmp_path / "temp.md").exists()


# ---------------------------------------------------------------------------
# Markdown output file
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_md_file_written(converter: DocumentConverter, tmp_csv: Path):
    """Conversion should write a .md file alongside or in output_dir."""
    result = await converter.convert(str(tmp_csv))

    md_path = Path(result.markdown_path)
    assert md_path.exists()
    assert md_path.suffix == ".md"
    assert md_path.read_text(encoding="utf-8") == result.markdown_content


# ---------------------------------------------------------------------------
# Supported types coverage
# ---------------------------------------------------------------------------


def test_supported_types_include_expected_extensions():
    """SUPPORTED_TYPES should include all documented extensions."""
    expected = {
        ".pdf", ".xlsx", ".xls", ".csv", ".docx", ".pptx",
        ".html", ".htm", ".txt",
        # Image OCR (solution 1)
        ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tiff", ".tif",
    }
    assert expected.issubset(set(DocumentConverter.SUPPORTED_TYPES.keys()))


# ---------------------------------------------------------------------------
# Image OCR (solution 1)
# ---------------------------------------------------------------------------


import shutil  # noqa: E402

_HAS_TESSERACT_BINARY = shutil.which("tesseract") is not None


@pytest.mark.asyncio
async def test_image_ocr_missing_pytesseract_raises_dependency_error(
    converter: DocumentConverter, tmp_path: Path
):
    """Without pytesseract installed, image OCR raises DependencyMissingError."""
    img = tmp_path / "photo.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)

    # Force the import of pytesseract to fail.
    import builtins

    real_import = builtins.__import__

    def _block_pytesseract(name, *args, **kwargs):
        if name == "pytesseract":
            raise ImportError("simulated missing pytesseract")
        return real_import(name, *args, **kwargs)

    with patch("builtins.__import__", side_effect=_block_pytesseract):
        with pytest.raises(DependencyMissingError) as exc_info:
            await converter.convert(str(img))

    msg = str(exc_info.value)
    assert "pytesseract" in msg
    assert "tesseract" in msg.lower()


@pytest.mark.asyncio
async def test_image_ocr_missing_tesseract_binary_raises_dependency_error(
    converter: DocumentConverter, tmp_path: Path
):
    """When pytesseract is installed but the system binary is missing,
    DependencyMissingError surfaces a helpful install instruction."""
    pytest.importorskip("pytesseract")
    pytest.importorskip("PIL")

    from PIL import Image

    img_path = tmp_path / "tiny.png"
    Image.new("RGB", (10, 10), color="white").save(img_path)

    import pytesseract

    with patch.object(
        pytesseract,
        "image_to_string",
        side_effect=pytesseract.TesseractNotFoundError(),
    ):
        with pytest.raises(DependencyMissingError) as exc_info:
            await converter.convert(str(img_path))

    msg = str(exc_info.value).lower()
    assert "tesseract" in msg
    assert ("apt" in msg) or ("brew" in msg) or ("install" in msg)


@pytest.mark.skipif(
    not _HAS_TESSERACT_BINARY,
    reason="tesseract system binary is not installed",
)
@pytest.mark.asyncio
async def test_image_ocr_extracts_text_from_image(
    converter: DocumentConverter, tmp_path: Path
):
    """End-to-end OCR test: render text into an image and verify it's extracted.

    Skipped when the tesseract binary is not on PATH (e.g. some CI runners).
    """
    pytest.importorskip("pytesseract")
    pytest.importorskip("PIL")

    from PIL import Image, ImageDraw, ImageFont

    img_path = tmp_path / "ocr-input.png"
    img = Image.new("RGB", (300, 80), color="white")
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("DejaVuSans.ttf", 28)
    except OSError:
        font = ImageFont.load_default()
    draw.text((10, 20), "OCR test 1234", fill="black", font=font)
    img.save(img_path)

    result = await converter.convert(str(img_path))

    assert result.file_type == "image"
    # OCR output sometimes garbles characters slightly; check digits + a
    # distinctive substring of the rendered text.
    md_lower = result.markdown_content.lower()
    assert "1234" in result.markdown_content
    assert "ocr" in md_lower


@pytest.mark.asyncio
async def test_image_ocr_no_text_returns_helpful_message(
    converter: DocumentConverter, tmp_path: Path
):
    """An image with no recognisable text yields a clear empty-result message,
    NOT a misleading hallucinated description."""
    pytest.importorskip("pytesseract")
    pytest.importorskip("PIL")

    from PIL import Image

    img_path = tmp_path / "blank.png"
    Image.new("RGB", (10, 10), color="white").save(img_path)

    import pytesseract

    # Force OCR to return an empty string regardless of binary state.
    with patch.object(pytesseract, "image_to_string", return_value="   \n  "):
        result = await converter.convert(str(img_path))

    assert result.file_type == "image"
    md = result.markdown_content
    assert "No text could be extracted" in md
    assert "vision" in md.lower()  # mentions vision-capable models as the alt


# ---------------------------------------------------------------------------
# Rows to markdown table helper
# ---------------------------------------------------------------------------


def test_rows_to_md_table_basic():
    """_rows_to_md_table should produce valid markdown table."""
    rows = [("A", "B"), ("1", "2"), ("3", "4")]
    table = DocumentConverter._rows_to_md_table(rows)
    assert "| A | B |" in table
    assert "| --- | --- |" in table
    assert "| 1 | 2 |" in table


def test_rows_to_md_table_none_values():
    """None values should be rendered as empty strings."""
    rows = [("A", "B"), (None, "2")]
    table = DocumentConverter._rows_to_md_table(rows)
    assert "|  | 2 |" in table


def test_rows_to_md_table_empty():
    """Empty rows should return empty string."""
    assert DocumentConverter._rows_to_md_table([]) == ""
