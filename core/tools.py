"""Shared tools for web searching, scraping, and intelligent data extraction.

All search/scrape/crawl/map operations use the Firecrawl API.
The tavily_research() function is preserved as a wrapper that uses
Firecrawl search + scrape + Claude synthesis to replicate deep research.
"""
from __future__ import annotations

import httpx
import asyncio
import hashlib
import json
from datetime import datetime
from typing import Optional
from bs4 import BeautifulSoup
from rich.console import Console

from .config import FIRECRAWL_API_KEY, ANTHROPIC_API_KEY, MODEL, MAX_TOKENS
from .cost_tracker import cost_tracker

console = Console()

# Firecrawl API base URLs
FIRECRAWL_V1_URL = "https://api.firecrawl.dev/v1"
FIRECRAWL_V2_URL = "https://api.firecrawl.dev/v2"

# Global list to track all sources used during research
_sources_registry: list[dict] = []

# Simple in-memory cache to avoid duplicate API calls
_search_cache: dict[str, any] = {}


def _firecrawl_headers() -> dict:
    return {
        "Authorization": f"Bearer {FIRECRAWL_API_KEY}",
        "Content-Type": "application/json",
    }


def clear_sources():
    """Clear the sources registry for a new research session."""
    global _sources_registry, _search_cache
    _sources_registry = []
    _search_cache = {}


def register_source(
    url: str,
    title: str,
    query: str,
    content_snippet: str = "",
    extraction_method: str = "unknown",
    content_length: int = 0,
):
    """Register a source that was used in research."""
    global _sources_registry
    existing = next((s for s in _sources_registry if s['url'] == url), None)
    if url and not existing:
        _sources_registry.append({
            'url': url,
            'title': title,
            'query': query,
            'snippet': content_snippet[:200] if content_snippet else "",
            'accessed': datetime.now().strftime("%Y-%m-%d %H:%M"),
            'extraction_method': extraction_method,
            'content_length': content_length,
        })
    elif url and existing:
        if content_length > existing.get('content_length', 0):
            existing['content_length'] = content_length
        if extraction_method != "unknown" and existing.get('extraction_method') == "unknown":
            existing['extraction_method'] = extraction_method
        if content_snippet and not existing.get('snippet'):
            existing['snippet'] = content_snippet[:200]


def get_all_sources() -> list[dict]:
    """Get all registered sources."""
    return _sources_registry.copy()


def _cache_key(prefix: str, *args) -> str:
    """Generate a cache key from arguments."""
    raw = f"{prefix}:{':'.join(str(a) for a in args)}"
    return hashlib.md5(raw.encode()).hexdigest()


# ---------------------------------------------------------------------------
#  SEARCH — Firecrawl /v1/search
# ---------------------------------------------------------------------------

async def search_web(query: str, max_results: int = 5, max_retries: int = 3) -> list[dict]:
    """Search the web using Firecrawl Search API with caching."""
    cache_k = _cache_key("search", query, max_results)
    if cache_k in _search_cache:
        console.print(f"[dim]  Cached: {query}[/dim]")
        return _search_cache[cache_k]

    console.print(f"[dim]  Searching: {query}[/dim]")

    for attempt in range(max_retries):
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{FIRECRAWL_V1_URL}/search",
                    headers=_firecrawl_headers(),
                    json={
                        "query": query,
                        "limit": max_results,
                        "scrapeOptions": {"formats": ["markdown"]},
                    },
                    timeout=60.0,
                )
                response.raise_for_status()
                data = response.json()

            cost_tracker.log_firecrawl_search()

            results = []
            for r in data.get("data", []):
                url = r.get("url", "")
                title = r.get("title", "")
                # Firecrawl search returns full markdown; use description as snippet
                # but keep full markdown available
                content = r.get("description", "")
                markdown = r.get("markdown", "")
                results.append({
                    "title": title,
                    "url": url,
                    "content": content,
                    "markdown": markdown,
                })
                if url:
                    register_source(url, title, query, content,
                                    extraction_method="firecrawl_search",
                                    content_length=len(markdown or content))

            _search_cache[cache_k] = results
            return results

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 429:
                wait = 30 * (attempt + 1)
                console.print(f"[yellow]  Search rate limited, waiting {wait}s (attempt {attempt + 1}/{max_retries})...[/yellow]")
                if attempt < max_retries - 1:
                    await asyncio.sleep(wait)
                else:
                    console.print(f"[red]  Search giving up after {max_retries} attempts: {query}[/red]")
                    return []
            else:
                console.print(f"[red]  Search HTTP error: {e}[/red]")
                return []
        except (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.ConnectError):
            if attempt < max_retries - 1:
                console.print(f"[yellow]  Search timeout, retrying ({attempt + 2}/{max_retries})...[/yellow]")
                await asyncio.sleep(2 ** attempt)
            else:
                console.print(f"[red]  Search failed after {max_retries} attempts: {query}[/red]")
                return []

    return []


# ---------------------------------------------------------------------------
#  SCRAPE — Firecrawl /v2/scrape
# ---------------------------------------------------------------------------

async def scrape_url(url: str, title: str = "Scraped Page") -> Optional[str]:
    """Scrape content from a URL using Firecrawl, with basic fallback."""
    cache_k = _cache_key("scrape", url)
    if cache_k in _search_cache:
        console.print(f"[dim]  Cached scrape: {url[:60]}[/dim]")
        return _search_cache[cache_k]

    console.print(f"[dim]  Scraping: {url}[/dim]")

    content = None
    if FIRECRAWL_API_KEY:
        content = await _scrape_with_firecrawl(url)
    else:
        content = await _scrape_basic(url)

    if content:
        method = "firecrawl" if FIRECRAWL_API_KEY else "basic_scrape"
        register_source(url, title, "direct_scrape", content[:200],
                        extraction_method=method, content_length=len(content))
        _search_cache[cache_k] = content

    return content


async def _scrape_with_firecrawl(url: str, max_retries: int = 2, include_branding: bool = False) -> Optional[str | dict]:
    """Scrape using Firecrawl v2 API."""
    formats = ["markdown"]
    if include_branding:
        formats.append("branding")

    for attempt in range(max_retries):
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{FIRECRAWL_V2_URL}/scrape",
                    headers=_firecrawl_headers(),
                    json={"url": url, "formats": formats, "maxAge": 600000},
                    timeout=45.0,
                )
                response.raise_for_status()
                data = response.json()
                result_data = data.get("data", {})
                content = result_data.get("markdown", "")
                if content:
                    console.print(f"[dim green]   Firecrawl: {len(content)} chars[/dim green]")
                    cost_tracker.log_firecrawl()

                if include_branding:
                    branding = result_data.get("branding")
                    if branding:
                        console.print(f"[dim green]   Firecrawl branding: extracted brand identity[/dim green]")
                    return {"markdown": content, "branding": branding}

                return content

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 429:
                wait = 30 * (attempt + 1)
                console.print(f"[yellow]   Firecrawl scrape rate limited, waiting {wait}s (attempt {attempt + 1}/{max_retries})...[/yellow]")
                if attempt < max_retries - 1:
                    await asyncio.sleep(wait)
                    continue
            console.print(f"[yellow]   Firecrawl scrape error, falling back to basic[/yellow]")
            if include_branding:
                basic = await _scrape_basic(url)
                return {"markdown": basic, "branding": None}
            return await _scrape_basic(url)
        except (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.ConnectError):
            if attempt < max_retries - 1:
                console.print(f"[yellow]   Firecrawl timeout, retrying...[/yellow]")
                await asyncio.sleep(2 ** attempt)
            else:
                console.print(f"[yellow]   Firecrawl failed, falling back to basic scrape[/yellow]")
                if include_branding:
                    basic = await _scrape_basic(url)
                    return {"markdown": basic, "branding": None}
                return await _scrape_basic(url)

        except Exception:
            console.print(f"[yellow]   Firecrawl error, falling back to basic[/yellow]")
            if include_branding:
                basic = await _scrape_basic(url)
                return {"markdown": basic, "branding": None}
            return await _scrape_basic(url)

    if include_branding:
        basic = await _scrape_basic(url)
        return {"markdown": basic, "branding": None}
    return await _scrape_basic(url)


async def _scrape_basic(url: str) -> Optional[str]:
    """Basic scraping with BeautifulSoup."""
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                url,
                headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"},
                timeout=30.0,
                follow_redirects=True,
            )
            response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")
        for element in soup(["script", "style", "nav", "footer", "header"]):
            element.decompose()

        text = soup.get_text(separator="\n", strip=True)
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        text = "\n".join(lines)

        if len(text) > 15000:
            text = text[:15000] + "\n\n[Content truncated...]"

        return text

    except Exception as e:
        console.print(f"[red]Failed to scrape {url}: {e}[/red]")
        return None


# ---------------------------------------------------------------------------
#  EXTRACT — Firecrawl batch scrape (replaces Tavily Extract)
# ---------------------------------------------------------------------------

async def tavily_extract(urls: list[str], query: str = None, max_retries: int = 2) -> list[dict]:
    """Extract content from URLs using Firecrawl scrape (drop-in for Tavily Extract)."""
    console.print(f"[dim]  Firecrawl Extract: {len(urls)} URLs[/dim]")

    async def _extract_one(url: str) -> dict:
        for attempt in range(max_retries):
            try:
                async with httpx.AsyncClient() as client:
                    response = await client.post(
                        f"{FIRECRAWL_V2_URL}/scrape",
                        headers=_firecrawl_headers(),
                        json={"url": url, "formats": ["markdown"], "maxAge": 600000},
                        timeout=45.0,
                    )
                    response.raise_for_status()
                    data = response.json()
                    content = data.get("data", {}).get("markdown", "")
                    if content:
                        console.print(f"[dim green]   Extracted: {len(content)} chars from {url[:50]}...[/dim green]")
                        cost_tracker.log_firecrawl()
                        register_source(url, "Firecrawl Extract", query or "direct_extract", content[:200],
                                        extraction_method="firecrawl_extract", content_length=len(content))
                    return {"url": url, "content": content, "success": bool(content)}
            except (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.ConnectError):
                if attempt < max_retries - 1:
                    await asyncio.sleep(2 ** attempt)
                else:
                    console.print(f"[yellow]   Failed: {url[:50]}[/yellow]")
                    return {"url": url, "content": "", "success": False}
            except Exception:
                console.print(f"[yellow]   Failed: {url[:50]}[/yellow]")
                return {"url": url, "content": "", "success": False}
        return {"url": url, "content": "", "success": False}

    results = await asyncio.gather(*[_extract_one(u) for u in urls], return_exceptions=True)
    return [r if isinstance(r, dict) else {"url": "", "content": "", "success": False} for r in results]


# ---------------------------------------------------------------------------
#  MAP — Firecrawl /v1/map (replaces Tavily Map)
# ---------------------------------------------------------------------------

async def tavily_map(
    url: str,
    instructions: str = None,
    max_depth: int = 1,
    limit: int = 50,
    max_retries: int = 2,
) -> dict:
    """Map a website using Firecrawl Map API — fast URL discovery."""
    console.print(f"[bold cyan]  Tavily Map: {url} (depth={max_depth}, limit={limit})[/bold cyan]")

    cache_k = _cache_key("map", url, instructions or "", max_depth, limit)
    if cache_k in _search_cache:
        console.print(f"[dim]   Using cached map result[/dim]")
        return _search_cache[cache_k]

    for attempt in range(max_retries):
        try:
            async with httpx.AsyncClient() as client:
                payload = {"url": url, "limit": limit}
                if instructions:
                    payload["search"] = instructions

                response = await client.post(
                    f"{FIRECRAWL_V1_URL}/map",
                    headers=_firecrawl_headers(),
                    json=payload,
                    timeout=60.0,
                )
                response.raise_for_status()
                data = response.json()

                urls_found = data.get("links", [])
                console.print(f"[green]   Mapped {len(urls_found)} URLs[/green]")

                cost_tracker.log_firecrawl_map(pages=len(urls_found))

                result = {"urls": urls_found, "results": urls_found, "base_url": url}
                _search_cache[cache_k] = result
                return result

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 429:
                wait = 30 * (attempt + 1)
                console.print(f"[yellow]   Firecrawl Map rate limited, waiting {wait}s (attempt {attempt + 1}/{max_retries})...[/yellow]")
                if attempt < max_retries - 1:
                    await asyncio.sleep(wait)
                else:
                    console.print(f"[red]   Firecrawl Map giving up after {max_retries} attempts[/red]")
                    return {"urls": [], "error": str(e)}
            else:
                console.print(f"[red]   Firecrawl Map error: {type(e).__name__}: {e}[/red]")
                return {"urls": [], "error": str(e)}
        except (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.ConnectError) as e:
            if attempt < max_retries - 1:
                console.print(f"[yellow]   Map timeout, retrying...[/yellow]")
                await asyncio.sleep(2 ** attempt)
            else:
                console.print(f"[red]   Firecrawl Map failed after {max_retries} attempts[/red]")
                return {"urls": [], "error": str(e)}
        except Exception as e:
            console.print(f"[red]   Firecrawl Map error: {type(e).__name__}: {e}[/red]")
            return {"urls": [], "error": str(e)}

    return {"urls": [], "error": "Max retries exceeded"}


# ---------------------------------------------------------------------------
#  CRAWL — Firecrawl /v1/crawl (replaces Tavily Crawl, async with polling)
# ---------------------------------------------------------------------------

async def tavily_crawl(
    url: str,
    instructions: str = None,
    max_depth: int = 2,
    limit: int = 30,
    extract_depth: str = "basic",
    max_retries: int = 2,
) -> dict:
    """Crawl a website using Firecrawl Crawl API — async with status polling."""
    console.print(f"[bold cyan]  Firecrawl Crawl: {url} (depth={max_depth}, limit={limit})[/bold cyan]")

    cache_k = _cache_key("crawl", url, instructions or "", max_depth, limit)
    if cache_k in _search_cache:
        console.print(f"[dim]   Using cached crawl result[/dim]")
        return _search_cache[cache_k]

    for attempt in range(max_retries):
        try:
            async with httpx.AsyncClient() as client:
                payload = {
                    "url": url,
                    "maxDepth": max_depth,
                    "limit": limit,
                    "scrapeOptions": {"formats": ["markdown"]},
                }

                # Start the crawl job
                response = await client.post(
                    f"{FIRECRAWL_V1_URL}/crawl",
                    headers=_firecrawl_headers(),
                    json=payload,
                    timeout=30.0,
                )
                response.raise_for_status()
                job_data = response.json()
                job_id = job_data.get("id")
                console.print(f"[dim]   Crawl job started: {job_id}[/dim]")

                # Poll for completion
                max_polls = 60
                poll_interval = 3

                for poll in range(max_polls):
                    await asyncio.sleep(poll_interval)

                    status_response = await client.get(
                        f"{FIRECRAWL_V1_URL}/crawl/{job_id}",
                        headers=_firecrawl_headers(),
                        timeout=30.0,
                    )
                    status_response.raise_for_status()
                    status_data = status_response.json()

                    status = status_data.get("status", "unknown")

                    if status == "completed":
                        crawl_results = status_data.get("data", [])
                        # Convert Firecrawl format to match expected format
                        results = []
                        for cr in crawl_results:
                            page_url = cr.get("metadata", {}).get("url", cr.get("url", ""))
                            content = cr.get("markdown", "")
                            title = cr.get("metadata", {}).get("title", page_url)
                            results.append({
                                "url": page_url,
                                "raw_content": content,
                                "title": title,
                            })

                        total_chars = sum(len(r.get("raw_content", "")) for r in results)
                        console.print(f"[green]   Crawled {len(results)} pages ({total_chars:,} chars)[/green]")

                        cost_tracker.log_firecrawl_crawl(pages=len(results))

                        for r in results:
                            page_url = r.get("url", "")
                            content = r.get("raw_content", "")
                            if page_url and content:
                                register_source(
                                    page_url, f"Crawled: {page_url}", "firecrawl_crawl",
                                    content[:200],
                                    extraction_method="firecrawl_crawl",
                                    content_length=len(content),
                                )

                        result = {"results": results, "base_url": url}
                        _search_cache[cache_k] = result
                        return result

                    elif status == "failed":
                        error = status_data.get("error", "Unknown error")
                        console.print(f"[red]   Crawl failed: {error}[/red]")
                        return {"results": [], "error": error}

                    else:
                        completed = status_data.get("completed", 0)
                        total = status_data.get("total", 0)
                        if poll % 5 == 0:
                            console.print(f"[dim]   Crawling... {completed}/{total} pages ({poll * poll_interval}s)[/dim]")

                console.print(f"[yellow]   Crawl timeout after {max_polls * poll_interval}s[/yellow]")
                return {"results": [], "error": "Timeout"}

        except (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.ConnectError) as e:
            if attempt < max_retries - 1:
                console.print(f"[yellow]   Crawl timeout, retrying...[/yellow]")
                await asyncio.sleep(2 ** attempt)
            else:
                console.print(f"[red]   Firecrawl Crawl failed after {max_retries} attempts[/red]")
                return {"results": [], "error": str(e)}
        except Exception as e:
            console.print(f"[red]   Firecrawl Crawl error: {type(e).__name__}: {e}[/red]")
            return {"results": [], "error": str(e)}

    return {"results": [], "error": "Max retries exceeded"}


# ---------------------------------------------------------------------------
#  BRANDING SCRAPE
# ---------------------------------------------------------------------------

async def scrape_with_branding(url: str, title: str = "Company Website") -> dict:
    """Scrape a URL and extract BOTH markdown content AND brand identity data."""
    console.print(f"[dim]  Scraping with branding: {url}[/dim]")

    cache_k = _cache_key("scrape_branding", url)
    if cache_k in _search_cache:
        console.print(f"[dim]   Using cached branding result[/dim]")
        return _search_cache[cache_k]

    if FIRECRAWL_API_KEY:
        result = await _scrape_with_firecrawl(url, include_branding=True)
        if isinstance(result, dict):
            markdown = result.get("markdown", "")
            if markdown:
                register_source(url, title, "brand_scrape", markdown[:200],
                                extraction_method="firecrawl_branding",
                                content_length=len(markdown))
            _search_cache[cache_k] = result
            return result

    basic = await _scrape_basic(url)
    result = {"markdown": basic or "", "branding": None}
    if basic:
        register_source(url, title, "brand_scrape", basic[:200],
                        extraction_method="basic_scrape",
                        content_length=len(basic))
        _search_cache[cache_k] = result
    return result


# ---------------------------------------------------------------------------
#  RESEARCH — Firecrawl search + scrape + Claude synthesis
#  (replaces Tavily Research API)
# ---------------------------------------------------------------------------

async def tavily_research(
    topic: str,
    model: str = "pro",
    output_schema: dict = None,
    max_retries: int = 2,
) -> dict:
    """Perform comprehensive research using Firecrawl search + Claude synthesis.

    Replaces the Tavily Research API. The `model` parameter controls depth:
    - "pro": 5 search queries, scrape top 3 results per query
    - "mini": 2 search queries, scrape top 2 results per query
    """
    console.print(f"[bold cyan]  Deep Research ({model}): {topic[:80]}...[/bold cyan]")

    cache_k = _cache_key("research", topic, model)
    if cache_k in _search_cache:
        console.print(f"[dim]   Using cached research result[/dim]")
        return _search_cache[cache_k]

    # Step 1: Generate search queries from the topic
    if model == "pro":
        search_limit = 5
        scrape_per_query = 3
    else:
        search_limit = 3
        scrape_per_query = 2

    # Search for the topic directly (Firecrawl search returns markdown)
    search_results = await search_web(topic, max_results=search_limit)

    if not search_results:
        return {"report": "", "sources": [], "error": "No search results"}

    # Step 2: Collect content — Firecrawl search already returns markdown
    source_texts = []
    sources = []
    for r in search_results:
        url = r.get("url", "")
        title = r.get("title", "")
        markdown = r.get("markdown", "")
        snippet = r.get("content", "")

        content = markdown if markdown else snippet
        if content and url:
            source_texts.append(f"### {title}\nSource: {url}\n\n{content[:6000]}")
            sources.append({"url": url, "title": title, "snippet": snippet[:200], "content": snippet})

    if not source_texts:
        return {"report": "", "sources": sources, "error": "No content extracted"}

    # Step 3: Synthesise with Claude
    combined_sources = "\n\n---\n\n".join(source_texts)

    schema_instruction = ""
    if output_schema:
        schema_instruction = f"\n\nReturn your response as JSON matching this schema:\n{json.dumps(output_schema, indent=2)}"

    synthesis_prompt = f"""You are a research analyst. Based on the following sources, write a comprehensive research report on this topic:

**Topic:** {topic}

**Sources:**

{combined_sources}

Write a thorough, well-structured report that synthesises all available information. Include specific facts, figures, names, and dates where available. Use numbered citations [1], [2], etc. referencing the sources.{schema_instruction}"""

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        response = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            messages=[{"role": "user", "content": synthesis_prompt}],
        )
        report = response.content[0].text

        cost_tracker.log_claude(
            response.usage.input_tokens,
            response.usage.output_tokens,
            label=f"research_synthesis:{topic[:40]}",
        )

        result = {
            "report": report,
            "sources": sources,
            "structured_output": None,
            "response_time": 0,
        }

        if output_schema:
            try:
                result["structured_output"] = json.loads(report)
            except json.JSONDecodeError:
                pass

        for source in sources:
            register_source(
                source.get("url", ""),
                source.get("title", "Research"),
                topic,
                source.get("snippet", "")[:200],
                extraction_method="firecrawl_research",
                content_length=len(source.get("content", "")),
            )

        _search_cache[cache_k] = result
        console.print(f"[green]   Research complete! ({len(report):,} chars)[/green]")
        return result

    except Exception as e:
        console.print(f"[red]   Research synthesis error: {type(e).__name__}: {e}[/red]")
        return {"report": "", "sources": sources, "error": str(e)}


# ---------------------------------------------------------------------------
#  BATCH + COMBINED OPERATIONS
# ---------------------------------------------------------------------------

async def batch_search_and_extract(
    queries: list[str],
    max_results: int = 3,
    deep_scrape_count: int = 2,
    max_concurrent: int = 8,
) -> list[str]:
    """Run multiple search_and_extract queries concurrently with rate limiting."""
    semaphore = asyncio.Semaphore(max_concurrent)

    async def _limited_search(query):
        async with semaphore:
            return await search_and_extract(
                query, max_results=max_results, deep_scrape_count=deep_scrape_count
            )

    tasks = [_limited_search(q) for q in queries]
    return await asyncio.gather(*tasks, return_exceptions=True)


async def search_and_extract(
    query: str,
    max_results: int = 3,
    deep_scrape: bool = True,
    deep_scrape_count: int = 2,
) -> str:
    """Search for a query and extract content from top results.

    Firecrawl search already returns full markdown, so we use that directly.
    Falls back to individual scraping if markdown is missing.
    """
    results = await search_web(query, max_results=max_results)

    content_parts = []

    for r in results:
        url = r.get("url", "")
        title = r.get("title", "")
        markdown = r.get("markdown", "")
        snippet = r.get("content", "")

        if markdown:
            content_parts.append(
                f"### {title}\nSource: {url}\nExtracted via: Firecrawl Search\n\n{markdown[:8000]}"
            )
        elif url:
            content_parts.append(f"### {title}\nSource: {url}\n\n{snippet}")
        else:
            content_parts.append(f"### {title}\n\n{snippet}")

    return "\n\n---\n\n".join(content_parts)
