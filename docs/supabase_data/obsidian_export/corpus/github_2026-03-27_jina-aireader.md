---
title: "jina-ai/reader"
source_type: github
source_url: "https://github.com/jina-ai/reader"
status: processed
fetch_timestamp: "2026-03-27T23:46:35.113923+00:00"
gemini_tokens_used: 6641
gemini_latency_ms: 15734
tags:
  - "source/github"
  - "domain/AI"
  - "domain/LLM"
  - "domain/Web Scraping"
  - "domain/Web Search"
  - "domain/API"
  - "domain/Natural Language Processing"
  - "domain/RAG"
  - "type/Tool"
  - "type/Reference"
  - "type/Documentation"
  - "difficulty/Intermediate"
  - "status/Processed"
  - "keyword/Jina AI"
  - "keyword/Reader API"
  - "keyword/LLM-friendly input"
  - "keyword/Web Scraping"
  - "keyword/Web Search"
  - "keyword/RAG"
  - "keyword/Content Extraction"
metadata:
  owner: "jina-ai"
  repo: "reader"
  description: "Convert any URL to an LLM-friendly input with a simple prefix https://r.jina.ai/"
  stars: "10395"
  forks: "784"
  language: "TypeScript"
  created_at: "2024-04-10T04:05:06Z"
  updated_at: "2026-03-27T14:40:52Z"
  license: "Apache-2.0"
  open_issues: "126"
  homepage: "https://jina.ai/reader"
---

# jina-ai/reader

> Jina AI's Reader is an API that converts any URL or web search query into an LLM-friendly input, facilitating improved agent and RAG systems by handling complex web content extraction and search automatically.

**Source:** [github](https://github.com/jina-ai/reader)

## Summary

## Project Overview
*   **Description**: Jina AI's Reader converts any URL into an LLM-friendly input by simply prepending `https://r.jina.ai/` to the URL.
*   **Languages**:
    *   TypeScript: 96.0%
    *   JavaScript: 3.8%
    *   Dockerfile: 0.2%

## Core Functionality
*   **Reader (`r.jina.ai`)**:
    *   Converts any URL (e.g., `https://r.jina.ai/https://your.url`) into an LLM-friendly format.
    *   Aims to provide improved input for agent and RAG systems at no cost.
*   **Search (`s.jina.ai`)**:
    *   Searches the web for a given query (e.g., `https://s.jina.ai/your+query`).
    *   Enables LLMs to access the latest world knowledge from the web.
    *   Behind the scenes, it searches the web, fetches the top 5 results, visits each URL, and applies `r.jina.ai` to extract content.
    *   Automates handling of browser rendering, blocking, JavaScript, and CSS issues.
*   **API Status**: The Reader API is free, stable, scalable, and actively maintained by Jina AI for production use. Rate limit information is available at `jina.ai/reader#pricing`.

## Key Features & Updates
*   **2024-07-15**: `s.jina.ai` now supports in-site search by setting the `site` query parameter (e.g., `site=jina.ai`).
*   **2024-05-30**: Reader can now process arbitrary PDF documents from any URL (e.g., `https://r.jina.ai/https://www.nasa.gov/wp-content/uploads/2023/01/55583main_vision_space_exploration2.pdf`).
*   **2024-05-15**: Introduced `s.jina.ai` endpoint for web search, returning top-5 results in an LLM-friendly format.
*   **2024-05-08**: Image captioning is disabled by default for better latency; it can be enabled by setting `x-with-generated-alt: true` in the request header.
*   **2024-04-24**: Introduced more fine-grained control over the Reader API using request headers, including forwarding cookies and using HTTP proxies.
*   **2024-04-15**: Reader supports image reading by captioning images at the specified URL and adding `Image [idx]: [caption]` as an alt tag (if missing), allowing LLMs to interact with images.

## Usage Details
*   **Using `r.jina.ai` for single URL fetching**: Prepend `https://r.jina.ai/` to any URL (e.g., `https://r.jina.ai/https://en.wikipedia.org/wiki/Artificial_intelligence`).
*   **Using `r.jina.ai` for a full website fetching**: A Google Colab example is provided.
*   **Using `s.jina.ai` for web search**: Prepend `https://s.jina.ai/` to a URL-encoded search query (e.g., `https://s.jina.ai/Who%20will%20win%202024%20US%20presidential%20election%3F`).
*   **Using `s.jina.ai` for in-site search**: Specify `site` in the query parameters (e.g., `curl 'https://s.jina.ai/When%20was%20Jina%20AI%20founded%3F?site=jina.ai&site=github.com'`).
*   **Interactive Code Snippet Builder**: A live demo and API form (`jina.ai/reader#apiform`) are recommended for exploring different parameter combinations.
*   **Using Request Headers**: Provides fine-grained control over API behavior:
    *   `x-with-generated-alt: true`: Enables image captioning.
    *   `x-set-cookie`: Forwards cookie settings (requests with cookies are not cached).
    *   `x-respond-with`: Bypasses `readability` filtering, with options:
        *   `markdown`: Returns markdown without `readability` processing.
        *   `html`: Returns `documentElement.outerHTML`.
        *   `text`: Returns `document.body.innerText`.
        *   `screenshot`: Returns the URL of the webpage's screenshot.
    *   `x-proxy-url`: Specifies a proxy server.
    *   `x-cache-tolerance`: Customizes cache tolerance (integer in seconds).
    *   `x-no-cache: true`: Bypasses the cached page (equivalent to `x-cache-tolerance: 0`).
    *   `x-target-selector`: Returns content within a matched CSS selector element.
    *   `x-wait-for-selector`: Waits until a matched CSS selector element is rendered before returning content.
*   **Using `r.jina.ai` for Single Page Application (SPA) fetching**:
    *   Natively supports SPAs using Puppeteer and headless Chrome.
    *   **SPAs with hash-based routing**: Use `POST` method with `url` parameter in the body (e.g., `curl -X POST 'https://r.jina.ai/' -d 'url=https://example.com/#/route'`).
    *   **SPAs with preloading contents**: Use `x-timeout` header to specify a wait time for network idle (e.g., `curl 'https://example.com/' -H 'x-timeout: 30'`).


## Related Notes
- [[github_2026-03-28_markuspfundsteinmcp-obsidian-a5204a]]

- [[youtube_2026-03-27_notebooklm-changed-completely-heres-what-matters-in-2026]]
