# 06 - Scraping System

## Overview
llm-use has an optional web scraping capability that allows workers to fetch and incorporate real web content into their responses. This is a form of RAG (Retrieval-Augmented Generation) at the worker level.

## Two-Phase Worker Scraping (lines 752-780)

When `--enable-scrape` is set, workers follow a 2-step process:

### Phase 1: Initial Call with URL Hint
The worker's task is augmented with: `"If you need sources, list up to N URLs prefixed with 'URL:'"`

### Phase 2: Grounding Follow-up
If the worker's response contains URLs:
1. URLs are extracted (first from `URL:` prefixed lines, then via regex)
2. URLs are scraped (max `--max-scrape-urls`, default 3)
3. A follow-up prompt combines the original task with scraped content
4. Worker produces a "grounded" answer

## Single-Mode Grounding (lines 782-793)
For single-mode execution, the orchestrator's response is also checked for URLs. If found, scraped content is used to "verify and improve" the answer.

## Scraping Backends

### requests + BeautifulSoup (default)
```python
r = requests.get(url, timeout=15, headers={"User-Agent": "llm-use/2.0"})
soup = BeautifulSoup(r.text, "html.parser")
for tag in soup(["script", "style", "noscript", "header", "footer", "nav", "form"]):
    tag.decompose()
text = " ".join(soup.get_text(separator=" ").split())
text = text[:4000]  # Hard limit
```

### Playwright (dynamic pages)
```python
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()
    page.goto(url, wait_until="domcontentloaded", timeout=15000)
    html = page.content()
    # Same BS4 processing...
```

## URL Extraction (lines 795-813)
Two-pass extraction:
1. Look for `URL:` prefixed lines (explicit worker output)
2. Fall back to regex: `https?://[^\s\)\]\}\>\"\']+`
3. Deduplicate and limit to `max_scrape_urls`

## Caching
All scraped content is cached in SQLite (`scrape_cache` table) by URL hash. No expiration.

## Key Patterns
- RAG-like pattern: LLM generates URLs, system fetches content, LLM refines answer
- Two scraping backends with graceful fallback
- Content truncation at 4000 chars per URL
- Scrape results cached to avoid repeated fetches

## Relevance to Our Project
Our `web_reader.py` skill does similar HTML extraction. Their approach of letting the LLM suggest URLs and then grounding with scraped content is interesting — it's a lightweight RAG pipeline without vector stores. We could adopt this pattern in our web_reader skill to enable automatic grounding.
