---
name: web-research
description: Search the internet to find solutions, documentation, tutorials, and best practices when you don't know how to do something. Use this when facing unfamiliar APIs, libraries, errors, or techniques that require up-to-date knowledge beyond the training cutoff.
disable-model-invocation: false
allowed-tools: WebSearch, WebFetch, Read, Write, Edit, Bash
---

# Web Research — Internet Search for Solutions

Search the web when you encounter something you don't know how to do, an unfamiliar error, or need current documentation.

## When to Use

- Unfamiliar library, API, or framework
- Error message you can't resolve from context alone
- Need current docs (versions, breaking changes, deprecations)
- Looking for best practices or architectural patterns
- Comparing tools, libraries, or approaches

## Critical Rules

1. **Search first, guess never** — if you're unsure, search before writing code
2. **Verify sources** — prefer official docs, GitHub repos, and Stack Overflow over random blogs
3. **Extract only what's needed** — don't dump entire pages, summarize the relevant solution
4. **Cite sources** — always tell the user where the info came from
5. **Cross-reference** — for critical decisions, check at least 2 sources

## Phase 1: Search

Use `WebSearch` with a targeted query. Tips for good queries:

- Include the **library name + version** when relevant
- Add **language** (python, typescript, etc.)
- Include the **error message** (quoted) for debugging
- Add the **current year** for recent results

### Examples

| Scenario | Query |
|----------|-------|
| Unknown API | `"FastAPI WebSocket" authentication middleware 2026` |
| Error | `python "ModuleNotFoundError" anthropic pip install` |
| How-to | `ollama openai-compatible API streaming python example` |
| Architecture | `multi-agent orchestration patterns LangGraph vs custom` |
| Comparison | `vLLM vs Ollama performance M1 Mac 2026` |

## Phase 2: Deep Dive (if needed)

If search results point to a useful page, use `WebFetch` to read it:

```
WebFetch(url="https://docs.example.com/guide", prompt="Extract the setup instructions and code examples for X")
```

Use focused prompts with WebFetch — ask for exactly what you need, not "summarize everything".

## Phase 3: Apply

1. **Summarize** the solution in 2-3 sentences for the user
2. **Show the source** — include the URL
3. **Adapt** the solution to the project's conventions (English, existing patterns, file structure)
4. **Implement** if the user asked for a fix, or **propose** if it's a design decision

## Fetching Twitter/X Content

X/Twitter blocks direct scraping. Use the **vxtwitter API** to fetch tweet content:

### Step 1: Convert the URL

Replace `x.com` or `twitter.com` with `api.vxtwitter.com`:

```
Original:  https://x.com/username/status/1234567890
API URL:   https://api.vxtwitter.com/username/status/1234567890
```

### Step 2: Fetch via WebFetch

```
WebFetch(
  url="https://api.vxtwitter.com/username/status/1234567890",
  prompt="Return the complete raw content: tweet text, username, date, any URLs or media links."
)
```

The API returns JSON with: `text`, `user_name`, `date`, `media` (image/video URLs), `likes`, `retweets`, `replies`.

### Step 3: Read images (if present)

If the tweet contains images with text (infographics, lists, code screenshots), use the `Read` tool on the image URLs from the media array to extract their content.

### Examples

| Want | Do |
|------|----|
| Read a tweet | `WebFetch` on `api.vxtwitter.com/user/status/ID` |
| Read a thread | Fetch each tweet in the thread by ID |
| Search tweets | `WebSearch` with `site:x.com "keyword"` or `from:username keyword` |
| Read tweet images | Get media URLs from API response, then `Read` them |

### Fallback chain

If `api.vxtwitter.com` fails:
1. Try `WebFetch` on `https://vxtwitter.com/user/status/ID` (HTML version)
2. Try `WebSearch` with the tweet URL or `from:username` + keywords
3. Try `WebFetch` on `https://threadreaderapp.com/user/username` for threads

## Common Research Patterns

### Debugging an Error
1. Search: `"exact error message" language framework`
2. Check top 3 results for matching context
3. If Stack Overflow: read the accepted answer + comments
4. If GitHub issue: check if there's a fix or workaround

### Learning a New API
1. Search: `library-name getting started guide`
2. WebFetch the official quickstart page
3. Search: `library-name + specific-feature example`
4. Adapt examples to project code

### Choosing Between Options
1. Search: `option-A vs option-B use-case year`
2. Look for benchmark comparisons
3. Check GitHub stars, last update, maintenance status
4. Recommend with trade-offs explained

## Output Format

When reporting findings to the user:

```
**Found**: [brief description of solution]

[Implementation or code snippet]

Source: [URL]
```

If nothing useful found after 3 searches, say so honestly and suggest alternative approaches.
