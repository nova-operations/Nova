import os
import json
import logging
import asyncio
from typing import Optional, List, Dict
import requests
from nova.tools.core.context_optimizer import (
    wrap_tool_output_optimization,
    optimize_search_results,
)

logger = logging.getLogger(__name__)


@wrap_tool_output_optimization
async def web_search(query: str, max_results: int = 5) -> str:
    """
    Performs a high-quality web search using Tavily API.

    Args:
        query: The search query.
        max_results: Number of results to return (default 5).

    Returns:
        JSON string of search results or error message.
    """
    api_key = os.getenv("TAVILY_API_KEY")
    if not api_key:
        return "Error: TAVILY_API_KEY is not set in the environment. Please add it to .env."

    try:
        url = "https://api.tavily.com/search"
        payload = {
            "api_key": api_key,
            "query": query,
            "search_depth": "smart",
            "include_answer": True,
            "include_images": False,
            "include_raw_content": False,
            "max_results": max_results,
        }

        # Use asyncio-friendly execution if possible, but requests is fine for a single call
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(
            None, lambda: requests.post(url, json=payload, timeout=20)
        )

        if response.status_code != 200:
            return f"Error from Tavily API: {response.status_code} - {response.text}"

        data = response.json()
        results = data.get("results", [])

        if not results:
            return f"No results found for '{query}'."

        # Format results nicely
        formatted_results = []
        for r in results:
            formatted_results.append(
                {
                    "title": r.get("title"),
                    "url": r.get("url"),
                    "content": r.get("content"),
                }
            )

        json_results = json.dumps(formatted_results, indent=2)

        # Aggressive optimization for search results
        return await optimize_search_results(json_results, max_tokens=8000)

    except Exception as e:
        logger.error(f"Web search error: {e}")
        return f"Error performing web search: {str(e)}"
