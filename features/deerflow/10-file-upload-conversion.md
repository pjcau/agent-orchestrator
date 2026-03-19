# Feature: File Upload & Document Conversion

## Context

From DeerFlow analysis (analysis/deepflow/07-tool-system.md, 29-learnings.md).
Agents should accept file uploads (PDF, Excel, Word, PPT) and auto-convert to Markdown for analysis. This enables document-heavy workflows (finance reports, data specs, marketing briefs).

## What to Build

### 1. File Upload API

```python
# In src/agent_orchestrator/dashboard/app.py

@app.post("/api/upload")
async def upload_file(file: UploadFile, session_id: str = Query(...)):
    """Upload a file and auto-convert to Markdown."""
    # 1. Save raw file to session directory
    # 2. Detect file type
    # 3. Convert to Markdown
    # 4. Return both raw path and markdown path
    ...
```

### 2. Document Converter

```python
# src/agent_orchestrator/core/document_converter.py

from dataclasses import dataclass

@dataclass
class ConvertedDocument:
    original_path: str
    markdown_path: str
    markdown_content: str
    file_type: str
    page_count: int | None = None

class DocumentConverter:
    """Convert common document formats to Markdown."""

    SUPPORTED_TYPES = {
        ".pdf": "_convert_pdf",
        ".xlsx": "_convert_excel",
        ".xls": "_convert_excel",
        ".csv": "_convert_csv",
        ".docx": "_convert_word",
        ".pptx": "_convert_powerpoint",
        ".html": "_convert_html",
        ".txt": "_convert_text",
    }

    async def convert(self, file_path: str) -> ConvertedDocument:
        """Auto-detect file type and convert to Markdown."""
        ext = Path(file_path).suffix.lower()
        converter = self.SUPPORTED_TYPES.get(ext)
        if not converter:
            raise UnsupportedFormatError(f"Cannot convert {ext} files")
        return await getattr(self, converter)(file_path)

    async def _convert_pdf(self, path: str) -> ConvertedDocument:
        """PDF → Markdown using pymupdf (fitz)."""
        ...

    async def _convert_excel(self, path: str) -> ConvertedDocument:
        """Excel → Markdown tables using openpyxl."""
        ...

    async def _convert_csv(self, path: str) -> ConvertedDocument:
        """CSV → Markdown table."""
        ...

    async def _convert_word(self, path: str) -> ConvertedDocument:
        """Word → Markdown using python-docx."""
        ...

    async def _convert_powerpoint(self, path: str) -> ConvertedDocument:
        """PowerPoint → Markdown (slide titles + bullet points) using python-pptx."""
        ...

    async def _convert_html(self, path: str) -> ConvertedDocument:
        """HTML → Markdown using markdownify or similar."""
        ...
```

### 3. Dependencies

Add optional document conversion dependencies:

```toml
# pyproject.toml
[project.optional-dependencies]
docs = [
    "pymupdf>=1.24",      # PDF
    "openpyxl>=3.1",       # Excel
    "python-docx>=1.1",    # Word
    "python-pptx>=0.6",    # PowerPoint
    "markdownify>=0.13",   # HTML
]
```

Graceful fallback: if a dependency is missing, return a clear error ("Install pymupdf for PDF support: pip install agent-orchestrator[docs]").

### 4. Per-Session File Storage

- Uploaded files stored in `jobs/job_<session_id>/uploads/`
- Converted markdown stored in `jobs/job_<session_id>/uploads/<filename>.md`
- Files available to the agent via `file_read` tool
- Cleaned up with session (existing auto-cleanup)

### 5. Dashboard Integration

- **Upload button** in the chat input area (next to existing file picker)
- **Progress indicator** during conversion
- **Preview**: show converted markdown in a modal before sending to agent
- **Attach to prompt**: converted markdown appended to the next user message

### 6. Size Limits

- Max file size: 10 MB
- Max pages (PDF): 50
- Max rows (Excel/CSV): 10,000

## Files to Modify

- **Create**: `src/agent_orchestrator/core/document_converter.py`
- **Modify**: `src/agent_orchestrator/dashboard/app.py` (upload endpoint)
- **Modify**: `pyproject.toml` (add docs optional dependency group)
- **Modify**: `src/agent_orchestrator/dashboard/static/` (upload UI)

## Tests

- Test PDF conversion to markdown
- Test Excel conversion to markdown table
- Test CSV conversion to markdown table
- Test Word conversion preserves headings and paragraphs
- Test PPT conversion extracts slide content
- Test unsupported format raises UnsupportedFormatError
- Test missing dependency gives helpful error message
- Test file size limit enforcement
- Test uploaded file stored in session directory
- Test converted markdown accessible via file_read

## Acceptance Criteria

- [ ] DocumentConverter with 6 format support
- [ ] Upload API endpoint with session storage
- [ ] Graceful fallback for missing dependencies
- [ ] Size limits enforced
- [ ] Dashboard upload UI
- [ ] All tests pass
- [ ] Existing tests still pass
