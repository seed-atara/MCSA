"""Shared tools for web searching, scraping, and intelligent data extraction."""
from __future__ import annotations

import httpx
import asyncio
import hashlib
import json
from datetime import datetime
from typing import Optional
from bs4 import BeautifulSoup
from rich.console import Console

from .config import TAVILY_API_KEY, FIRECRAWL_API_KEY
from .cost_tracker import cost_tracker

console = Console()

# Tavily API base URL
TAVILY_BASE_URL = "https://api.tavily.com"

# Global list to track all sources used during research
_sources_registry: list[dict] = []

# Simple in-memory cache to avoid duplicate API calls
_search_cache: dict[str, any] = {}


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


async def search_web(query: str, max_results: int = 5, max_retries: int = 3) -> list[dict]:
    """Search the web using Tavily API with caching."""
    cache_k = _cache_key("search", query, max_results)
    if cache_k in _search_cache:
        console.print(f"[dim]  Cached: {query}[/dim]")
        return _search_cache[cache_k]

    console.print(f"[dim]  Searching: {query}[/dim]")

    for attempt in range(max_retries):
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    "https://api.tavily.com/search",
                    json={
                        "api_key": TAVILY_API_KEY,
                        "query": query,
                        "max_results": max_results,
                        "include_answer": True,
                        "include_raw_content": False,
                    },
                    timeout=60.0,
                )
                response.raise_for_status()
                data = response.json()

            cost_tracker.log_tavily_search()

            results = []
            for r in data.get("results", []):
                url = r.get("url", "")
                title = r.get("title", "")
                content = r.get("content", "")
                results.append({"title": title, "url": url, "content": content})
                if url:
                    register_source(url, title, query, content,
                                    extraction_method="tavily_search",
                                    content_length=len(content))

            if data.get("answer"):
                results.insert(0, {"title": "Summary", "url": "", "content": data["answer"]})

            _search_cache[cache_k] = results
            return results

        except (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.ConnectError):
            if attempt < max_retries - 1:
                console.print(f"[yellow]  Search timeout, retrying ({attempt + 2}/{max_retries})...[/yellow]")
                await asyncio.sleep(2 ** attempt)
            else:
                console.print(f"[red]  Search failed after {max_retries} attempts: {query}[/red]")
                return []

    return []


async def scrape_url(url: str, title: str = "Scraped Page") -> Optional[str]:
    """Scrape content from a URL. Uses Firecrawl if available, otherwise basic."""
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
                    "https://api.firecrawl.dev/v2/scrape",
                    headers={"Authorization": f"Bearer {FIRECRAWL_API_KEY}"},
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


async def tavily_extract(urls: list[str], query: str = None, max_retries: int = 2) -> list[dict]:
    """Extract content from URLs using Tavily Extract API."""
    console.print(f"[dim]  Tavily Extract: {len(urls)} URLs[/dim]")

    for attempt in range(max_retries):
        try:
            async with httpx.AsyncClient() as client:
                payload = {"urls": urls, "extract_depth": "advanced", "format": "markdown"}
                if query:
                    payload["query"] = query
                    payload["chunks_per_source"] = 5

                response = await client.post(
                    f"{TAVILY_BASE_URL}/extract",
                    headers={"Authorization": f"Bearer {TAVILY_API_KEY}"},
                    json=payload,
                    timeout=60.0,
                )
                response.raise_for_status()
                data = response.json()

            successful_urls = sum(1 for r in data.get("results", []) if r.get("raw_content"))
            cost_tracker.log_tavily_extract(url_count=successful_urls)

            results = []
            for r in data.get("results", []):
                url = r.get("url", "")
                content = r.get("raw_content", "")
                if content:
                    console.print(f"[dim green]   Extracted: {len(content)} chars from {url[:50]}...[/dim green]")
                    register_source(url, "Tavily Extract", query or "direct_extract", content[:200],
                                    extraction_method="tavily_extract", content_length=len(content))
                results.append({"url": url, "content": content, "success": bool(content)})

            for f in data.get("failed_results", []):
                console.print(f"[yellow]   Failed: {f.get('url', 'unknown')}[/yellow]")
                results.append({"url": f.get("url", ""), "content": "", "success": False})

            return results

        except (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.ConnectError):
            if attempt < max_retries - 1:
                console.print(f"[yellow]   Tavily Extract timeout, retrying...[/yellow]")
                await asyncio.sleep(2 ** attempt)
            else:
                console.print(f"[red]   Tavily Extract failed after {max_retries} attempts[/red]")
                return [{"url": u, "content": "", "success": False} for u in urls]
        except Exception:
            console.print(f"[red]   Tavily Extract error[/red]")
            return [{"url": u, "content": "", "success": False} for u in urls]

    return []


async def tavily_research(
    topic: str,
    model: str = "pro",
    output_schema: dict = None,
    max_retries: int = 2,
) -> dict:
    """Perform comprehensive research on a topic using Tavily Research API."""
    console.print(f"[bold cyan]  Tavily Research ({model}): {topic[:80]}...[/bold cyan]")

    cache_k = _cache_key("research", topic, model)
    if cache_k in _search_cache:
        console.print(f"[dim]   Using cached research result[/dim]")
        return _search_cache[cache_k]

    for attempt in range(max_retries):
        try:
            async with httpx.AsyncClient() as client:
                payload = {
                    "input": topic,
                    "model": model,
                    "citation_format": "numbered",
                    "stream": False,
                }
                if output_schema:
                    payload["output_schema"] = output_schema

                response = await client.post(
                    f"{TAVILY_BASE_URL}/research",
                    headers={"Authorization": f"Bearer {TAVILY_API_KEY}"},
                    json=payload,
                    timeout=30.0,
                )
                response.raise_for_status()
                task_data = response.json()

                request_id = task_data.get("request_id")
                console.print(f"[dim]   Research task created: {request_id}[/dim]")

                max_polls = 60
                poll_interval = 5

                for poll in range(max_polls):
                    await asyncio.sleep(poll_interval)

                    status_response = await client.get(
                        f"{TAVILY_BASE_URL}/research/{request_id}",
                        headers={"Authorization": f"Bearer {TAVILY_API_KEY}"},
                        timeout=30.0,
                    )
                    status_response.raise_for_status()
                    status_data = status_response.json()

                    status = status_data.get("status", "unknown")

                    if status == "completed":
                        console.print(f"[green]   Research complete![/green]")
                        cost_tracker.log_tavily_research(model=model)

                        for source in status_data.get("sources", []):
                            snippet = source.get("snippet", source.get("content", ""))
                            register_source(
                                source.get("url", ""),
                                source.get("title", "Tavily Research"),
                                topic,
                                snippet[:200],
                                extraction_method="tavily_research",
                                content_length=len(snippet),
                            )

                        result = {
                            "report": status_data.get("content", status_data.get("report", "")),
                            "sources": status_data.get("sources", []),
                            "structured_output": status_data.get("structured_output"),
                            "response_time": status_data.get("response_time", 0),
                        }

                        _search_cache[cache_k] = result
                        return result

                    elif status == "failed":
                        console.print(f"[red]   Research failed: {status_data.get('error', 'Unknown error')}[/red]")
                        return {"report": "", "sources": [], "error": status_data.get("error")}

                    else:
                        if poll % 6 == 0:
                            console.print(f"[dim]   Still researching... ({poll * poll_interval}s)[/dim]")

                console.print(f"[yellow]   Research timeout after {max_polls * poll_interval}s[/yellow]")
                return {"report": "", "sources": [], "error": "Timeout"}

        except (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.ConnectError) as e:
            if attempt < max_retries - 1:
                console.print(f"[yellow]   Connection error, retrying...[/yellow]")
                await asyncio.sleep(2 ** attempt)
            else:
                console.print(f"[red]   Tavily Research failed after {max_retries} attempts[/red]")
                return {"report": "", "sources": [], "error": str(e)}
        except Exception as e:
            console.print(f"[red]   Tavily Research error: {type(e).__name__}: {e}[/red]")
            return {"report": "", "sources": [], "error": str(e)}

    return {"report": "", "sources": [], "error": "Max retries exceeded"}


async def tavily_crawl(
    url: str,
    instructions: str = None,
    max_depth: int = 2,
    limit: int = 30,
    extract_depth: str = "basic",
    max_retries: int = 2,
) -> dict:
    """Crawl a website using Tavily Crawl API — graph-based traversal."""
    console.print(f"[bold cyan]  Tavily Crawl: {url} (depth={max_depth}, limit={limit})[/bold cyan]")

    cache_k = _cache_key("crawl", url, instructions or "", max_depth, limit)
    if cache_k in _search_cache:
        console.print(f"[dim]   Using cached crawl result[/dim]")
        return _search_cache[cache_k]

    for attempt in range(max_retries):
        try:
            async with httpx.AsyncClient() as client:
                payload = {
                    "url": url,
                    "max_depth": max_depth,
                    "limit": limit,
                    "extract_depth": extract_depth,
                    "format": "markdown",
                }
                if instructions:
                    payload["instructions"] = instructions

                response = await client.post(
                    f"{TAVILY_BASE_URL}/crawl",
                    headers={"Authorization": f"Bearer {TAVILY_API_KEY}"},
                    json=payload,
                    timeout=120.0,
                )
                response.raise_for_status()
                data = response.json()

                results = data.get("results", [])
                total_chars = sum(len(r.get("raw_content", "")) for r in results)
                console.print(f"[green]   Crawled {len(results)} pages ({total_chars:,} chars)[/green]")

                pages_crawled = len(results)
                cost_tracker.log_tavily_crawl(pages=pages_crawled, extract_depth=extract_depth,
                                              has_instructions=bool(instructions))

                for r in results:
                    page_url = r.get("url", "")
                    content = r.get("raw_content", "")
                    if page_url and content:
                        register_source(
                            page_url, f"Crawled: {page_url}", "tavily_crawl",
                            content[:200],
                            extraction_method="tavily_crawl",
                            content_length=len(content),
                        )

                result = {"results": results, "base_url": data.get("base_url", url)}
                _search_cache[cache_k] = result
                return result

        except (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.ConnectError) as e:
            if attempt < max_retries - 1:
                console.print(f"[yellow]   Crawl timeout, retrying...[/yellow]")
                await asyncio.sleep(2 ** attempt)
            else:
                console.print(f"[red]   Tavily Crawl failed after {max_retries} attempts[/red]")
                return {"results": [], "error": str(e)}
        except Exception as e:
            console.print(f"[red]   Tavily Crawl error: {type(e).__name__}: {e}[/red]")
            return {"results": [], "error": str(e)}

    return {"results": [], "error": "Max retries exceeded"}


async def tavily_map(
    url: str,
    instructions: str = None,
    max_depth: int = 1,
    limit: int = 50,
    max_retries: int = 2,
) -> dict:
    """Map a website using Tavily Map API — fast URL discovery without content extraction."""
    console.print(f"[bold cyan]  Tavily Map: {url} (depth={max_depth}, limit={limit})[/bold cyan]")

    cache_k = _cache_key("map", url, instructions or "", max_depth, limit)
    if cache_k in _search_cache:
        console.print(f"[dim]   Using cached map result[/dim]")
        return _search_cache[cache_k]

    for attempt in range(max_retries):
        try:
            async with httpx.AsyncClient() as client:
                payload = {"url": url, "max_depth": max_depth, "limit": limit}
                if instructions:
                    payload["instructions"] = instructions

                response = await client.post(
                    f"{TAVILY_BASE_URL}/map",
                    headers={"Authorization": f"Bearer {TAVILY_API_KEY}"},
                    json=payload,
                    timeout=60.0,
                )
                response.raise_for_status()
                data = response.json()

                results = data.get("results", [])
                # Tavily Map may return plain URL strings or dicts with "url" key
                urls_found = []
                for r in results:
                    if isinstance(r, str):
                        urls_found.append(r)
                    elif isinstance(r, dict) and r.get("url"):
                        urls_found.append(r["url"])
                console.print(f"[green]   Mapped {len(urls_found)} URLs[/green]")

                cost_tracker.log_tavily_map(pages=len(urls_found),
                                            has_instructions=bool(instructions))

                result = {"urls": urls_found, "results": results, "base_url": data.get("base_url", url)}
                _search_cache[cache_k] = result
                return result

        except (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.ConnectError) as e:
            if attempt < max_retries - 1:
                console.print(f"[yellow]   Map timeout, retrying...[/yellow]")
                await asyncio.sleep(2 ** attempt)
            else:
                console.print(f"[red]   Tavily Map failed after {max_retries} attempts[/red]")
                return {"urls": [], "error": str(e)}
        except Exception as e:
            console.print(f"[red]   Tavily Map error: {type(e).__name__}: {e}[/red]")
            return {"urls": [], "error": str(e)}

    return {"urls": [], "error": "Max retries exceeded"}


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


async def _firecrawl_batch_extract(urls: list[str], query: str = None) -> dict[str, str]:
    """Extract content from multiple URLs using Firecrawl v2, run concurrently."""
    if not FIRECRAWL_API_KEY or not urls:
        return {}

    console.print(f"[dim]  Firecrawl Extract: {len(urls)} URLs[/dim]")

    async def _extract_one(url: str) -> tuple[str, str]:
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    "https://api.firecrawl.dev/v2/scrape",
                    headers={"Authorization": f"Bearer {FIRECRAWL_API_KEY}"},
                    json={"url": url, "formats": ["markdown"], "maxAge": 600000},
                    timeout=45.0,
                )
                response.raise_for_status()
                data = response.json()
                content = data.get("data", {}).get("markdown", "")
                if content:
                    console.print(f"[dim green]   Firecrawl: {len(content)} chars from {url[:50]}...[/dim green]")
                    cost_tracker.log_firecrawl()
                    register_source(url, "Firecrawl Extract", query or "firecrawl_extract", content[:200],
                                    extraction_method="firecrawl", content_length=len(content))
                return (url, content)
        except Exception:
            console.print(f"[yellow]   Firecrawl failed for {url[:50]}[/yellow]")
            return (url, "")

    results = await asyncio.gather(*[_extract_one(u) for u in urls], return_exceptions=True)

    extracted = {}
    for r in results:
        if isinstance(r, tuple):
            url, content = r
            if content:
                extracted[url] = content

    return extracted


async def search_and_extract(
    query: str,
    max_results: int = 3,
    deep_scrape: bool = True,
    deep_scrape_count: int = 2,
) -> str:
    """Search for a query and extract content from top results.

    Runs BOTH Tavily Extract and Firecrawl in parallel when available,
    then merges results.
    """
    results = await search_web(query, max_results=max_results)

    content_parts = []

    if deep_scrape:
        urls_to_extract = [r["url"] for r in results if r.get("url")][:deep_scrape_count]

        if urls_to_extract:
            extraction_tasks = [tavily_extract(urls_to_extract, query=query)]
            if FIRECRAWL_API_KEY:
                extraction_tasks.append(_firecrawl_batch_extract(urls_to_extract, query=query))

            extraction_results = await asyncio.gather(*extraction_tasks, return_exceptions=True)

            tavily_map_results: dict[str, str] = {}
            if not isinstance(extraction_results[0], Exception):
                tavily_map_results = {
                    e["url"]: e["content"]
                    for e in extraction_results[0]
                    if e.get("success") and e.get("content")
                }

            firecrawl_map_results: dict[str, str] = {}
            if len(extraction_results) > 1 and not isinstance(extraction_results[1], Exception):
                firecrawl_map_results = extraction_results[1]

            if len(firecrawl_map_results) > 0:
                tavily_count = len(tavily_map_results)
                firecrawl_count = len(firecrawl_map_results)
                console.print(f"[dim]   Extraction: Tavily={tavily_count}, Firecrawl={firecrawl_count} URLs[/dim]")

            for r in results:
                url = r.get("url", "")
                tavily_content = tavily_map_results.get(url, "")
                firecrawl_content = firecrawl_map_results.get(url, "")

                if tavily_content and firecrawl_content:
                    if len(firecrawl_content) > len(tavily_content):
                        primary, secondary, primary_label, secondary_label = (
                            firecrawl_content, tavily_content, "Firecrawl", "Tavily"
                        )
                    else:
                        primary, secondary, primary_label, secondary_label = (
                            tavily_content, firecrawl_content, "Tavily", "Firecrawl"
                        )

                    content_parts.append(
                        f"### {r['title']}\n"
                        f"Source: {url}\n"
                        f"Extracted via: {primary_label} (primary), {secondary_label} (corroborated)\n\n"
                        f"{primary[:8000]}\n\n"
                        f"--- Additional extraction ({secondary_label}) ---\n\n"
                        f"{secondary[:4000]}"
                    )
                elif tavily_content:
                    content_parts.append(
                        f"### {r['title']}\nSource: {url}\nExtracted via: Tavily\n\n{tavily_content[:8000]}"
                    )
                elif firecrawl_content:
                    content_parts.append(
                        f"### {r['title']}\nSource: {url}\nExtracted via: Firecrawl\n\n{firecrawl_content[:8000]}"
                    )
                elif url:
                    content_parts.append(f"### {r['title']}\nSource: {url}\n\n{r['content']}")
                else:
                    content_parts.append(f"### {r['title']}\n\n{r['content']}")
        else:
            for r in results:
                if r.get("url"):
                    content_parts.append(f"### {r['title']}\nSource: {r['url']}\n\n{r['content']}")
                else:
                    content_parts.append(f"### {r['title']}\n\n{r['content']}")
    else:
        for r in results:
            if r.get("url"):
                content_parts.append(f"### {r['title']}\nSource: {r['url']}\n\n{r['content']}")
            else:
                content_parts.append(f"### {r['title']}\n\n{r['content']}")

    return "\n\n---\n\n".join(content_parts)
