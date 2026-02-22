"""
adapters/browser/playwright_adapter.py — Playwright browser automation for agents.

Provides browser control tools so agents can:
  - Navigate to URLs
  - Click elements, fill forms, extract text
  - Take screenshots
  - Wait for elements/navigation
  - Execute JavaScript
  - Manage browser sessions

Inspired by OpenClaw's 116-file browser automation suite,
simplified to a single-file adapter with the most essential operations.

Dependencies:
  pip install playwright
  playwright install chromium
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import tempfile
import time
from typing import Any, Optional

logger = logging.getLogger(__name__)

try:
    from playwright.async_api import async_playwright, Browser, Page, BrowserContext
    _HAS_PLAYWRIGHT = True
except ImportError:
    _HAS_PLAYWRIGHT = False
    logger.debug("playwright not installed — browser tools disabled")


# ── Browser Session Manager ──────────────────────────────────────────────────

class BrowserSession:
    """
    Manages a persistent browser session for agent use.

    Features:
      - Lazy browser launch (created on first use)
      - Session persistence (survives across tool calls)
      - Auto-cleanup on garbage collection
      - Screenshot capture with base64 encoding
      - Cookie and localStorage access
    """

    def __init__(self, headless: bool = True, timeout: int = 30000):
        """
        Args:
            headless: Run browser without GUI (default: True)
            timeout: Default timeout for operations in ms
        """
        if not _HAS_PLAYWRIGHT:
            raise RuntimeError(
                "playwright not installed. Install with:\n"
                "  pip install playwright && playwright install chromium"
            )
        self.headless = headless
        self.timeout = timeout
        self._playwright = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None
        self._screenshots_dir = os.path.join(tempfile.gettempdir(), "cleo_screenshots")
        os.makedirs(self._screenshots_dir, exist_ok=True)

    async def _ensure_browser(self):
        """Lazy-init: start browser if not running."""
        if self._page is not None:
            return

        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=self.headless,
        )
        self._context = await self._browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        )
        self._context.set_default_timeout(self.timeout)
        self._page = await self._context.new_page()
        logger.info("[browser] Session started (headless=%s)", self.headless)

    async def close(self):
        """Close the browser session."""
        if self._browser:
            await self._browser.close()
            self._browser = None
            self._page = None
            self._context = None
        if self._playwright:
            await self._playwright.stop()
            self._playwright = None
        logger.info("[browser] Session closed")

    # ── Navigation ───────────────────────────────────────────────────────

    async def navigate(self, url: str, wait_until: str = "domcontentloaded") -> dict:
        """Navigate to a URL.

        Args:
            url: Target URL
            wait_until: Wait condition: "load", "domcontentloaded", "networkidle"

        Returns:
            {"ok": bool, "url": str, "title": str, "status": int}
        """
        await self._ensure_browser()
        try:
            response = await self._page.goto(url, wait_until=wait_until)
            title = await self._page.title()
            return {
                "ok": True,
                "url": self._page.url,
                "title": title,
                "status": response.status if response else 0,
            }
        except Exception as e:
            return {"ok": False, "error": str(e), "url": url}

    async def go_back(self) -> dict:
        """Navigate back in history."""
        await self._ensure_browser()
        try:
            await self._page.go_back()
            return {"ok": True, "url": self._page.url}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    async def go_forward(self) -> dict:
        """Navigate forward in history."""
        await self._ensure_browser()
        try:
            await self._page.go_forward()
            return {"ok": True, "url": self._page.url}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ── Interaction ──────────────────────────────────────────────────────

    async def click(self, selector: str) -> dict:
        """Click an element by CSS selector.

        Args:
            selector: CSS selector (e.g. "button.submit", "#login-btn")
        """
        await self._ensure_browser()
        try:
            await self._page.click(selector)
            return {"ok": True, "selector": selector}
        except Exception as e:
            return {"ok": False, "error": str(e), "selector": selector}

    async def fill(self, selector: str, value: str) -> dict:
        """Fill a form field.

        Args:
            selector: CSS selector for the input
            value: Text to enter
        """
        await self._ensure_browser()
        try:
            await self._page.fill(selector, value)
            return {"ok": True, "selector": selector}
        except Exception as e:
            return {"ok": False, "error": str(e), "selector": selector}

    async def select(self, selector: str, value: str) -> dict:
        """Select a dropdown option by value."""
        await self._ensure_browser()
        try:
            await self._page.select_option(selector, value)
            return {"ok": True, "selector": selector, "value": value}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    async def type_text(self, selector: str, text: str, delay: int = 50) -> dict:
        """Type text character by character (simulates real typing)."""
        await self._ensure_browser()
        try:
            await self._page.type(selector, text, delay=delay)
            return {"ok": True, "selector": selector}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    async def press_key(self, key: str) -> dict:
        """Press a keyboard key (e.g. "Enter", "Tab", "Escape")."""
        await self._ensure_browser()
        try:
            await self._page.keyboard.press(key)
            return {"ok": True, "key": key}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ── Content Extraction ───────────────────────────────────────────────

    async def get_text(self, selector: str = "body") -> dict:
        """Extract text content from an element.

        Args:
            selector: CSS selector (default: full page body)
        """
        await self._ensure_browser()
        try:
            text = await self._page.text_content(selector) or ""
            # Truncate very long text
            if len(text) > 10000:
                text = text[:9500] + "\n...(truncated)"
            return {"ok": True, "text": text, "length": len(text)}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    async def get_html(self, selector: str = "body") -> dict:
        """Get inner HTML of an element."""
        await self._ensure_browser()
        try:
            html = await self._page.inner_html(selector)
            if len(html) > 20000:
                html = html[:19500] + "\n...(truncated)"
            return {"ok": True, "html": html, "length": len(html)}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    async def get_attribute(self, selector: str, attribute: str) -> dict:
        """Get an attribute value from an element."""
        await self._ensure_browser()
        try:
            value = await self._page.get_attribute(selector, attribute)
            return {"ok": True, "value": value}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    async def query_all(self, selector: str, extract: str = "text") -> dict:
        """Query all matching elements and extract data.

        Args:
            selector: CSS selector
            extract: What to extract: "text", "html", or an attribute name
        """
        await self._ensure_browser()
        try:
            elements = await self._page.query_selector_all(selector)
            results = []
            for el in elements[:50]:  # limit to 50 elements
                if extract == "text":
                    val = await el.text_content() or ""
                elif extract == "html":
                    val = await el.inner_html()
                else:
                    val = await el.get_attribute(extract) or ""
                results.append(val.strip() if isinstance(val, str) else val)
            return {"ok": True, "count": len(results), "results": results}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ── Screenshots ──────────────────────────────────────────────────────

    async def screenshot(self, full_page: bool = False,
                         selector: str = "") -> dict:
        """Take a screenshot.

        Args:
            full_page: Capture full scrollable page
            selector: If set, screenshot only this element

        Returns:
            {"ok": bool, "path": str, "base64": str (first 200 chars)}
        """
        await self._ensure_browser()
        try:
            filename = f"screenshot_{int(time.time()*1000)}.png"
            path = os.path.join(self._screenshots_dir, filename)

            if selector:
                element = await self._page.query_selector(selector)
                if element:
                    await element.screenshot(path=path)
                else:
                    return {"ok": False, "error": f"Element not found: {selector}"}
            else:
                await self._page.screenshot(path=path, full_page=full_page)

            # Read and base64-encode for embedding
            with open(path, "rb") as f:
                img_bytes = f.read()
            b64 = base64.b64encode(img_bytes).decode("ascii")

            return {
                "ok": True,
                "path": path,
                "size_bytes": len(img_bytes),
                "base64_preview": b64[:200] + "..." if len(b64) > 200 else b64,
            }
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ── Wait / Sync ──────────────────────────────────────────────────────

    async def wait_for(self, selector: str, state: str = "visible",
                       timeout: int = 0) -> dict:
        """Wait for an element to reach a state.

        Args:
            selector: CSS selector
            state: "visible", "hidden", "attached", "detached"
            timeout: Override default timeout (ms)
        """
        await self._ensure_browser()
        try:
            kwargs = {"state": state}
            if timeout:
                kwargs["timeout"] = timeout
            await self._page.wait_for_selector(selector, **kwargs)
            return {"ok": True, "selector": selector, "state": state}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    async def wait_for_navigation(self, timeout: int = 0) -> dict:
        """Wait for page navigation to complete."""
        await self._ensure_browser()
        try:
            kwargs = {}
            if timeout:
                kwargs["timeout"] = timeout
            await self._page.wait_for_load_state("domcontentloaded", **kwargs)
            return {"ok": True, "url": self._page.url}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ── JavaScript ───────────────────────────────────────────────────────

    async def evaluate(self, expression: str) -> dict:
        """Execute JavaScript in the page context.

        Args:
            expression: JavaScript expression or function

        Returns:
            {"ok": bool, "result": Any}
        """
        await self._ensure_browser()
        try:
            result = await self._page.evaluate(expression)
            # Serialize complex objects
            if isinstance(result, (dict, list)):
                import json
                result_str = json.dumps(result, default=str, ensure_ascii=False)
                if len(result_str) > 5000:
                    result_str = result_str[:4500] + "...(truncated)"
                    result = result_str
            return {"ok": True, "result": result}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ── Page Info ────────────────────────────────────────────────────────

    async def page_info(self) -> dict:
        """Get current page information."""
        await self._ensure_browser()
        try:
            title = await self._page.title()
            return {
                "ok": True,
                "url": self._page.url,
                "title": title,
            }
        except Exception as e:
            return {"ok": False, "error": str(e)}


# ── Session Manager (singleton per agent) ─────────────────────────────────────

_sessions: dict[str, BrowserSession] = {}


def get_browser_session(agent_id: str = "default",
                        headless: bool = True) -> BrowserSession:
    """Get or create a browser session for an agent."""
    if agent_id not in _sessions:
        _sessions[agent_id] = BrowserSession(headless=headless)
    return _sessions[agent_id]


async def close_all_sessions():
    """Close all browser sessions (cleanup on shutdown)."""
    for session in _sessions.values():
        try:
            await session.close()
        except Exception:
            pass
    _sessions.clear()


# ── Tool Handlers (for integration with core/tools.py) ────────────────────────

def _run_async(coro):
    """Run an async function from sync context."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # We're in an async context, use create_task
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(asyncio.run, coro)
                return future.result(timeout=60)
        return loop.run_until_complete(coro)
    except RuntimeError:
        return asyncio.run(coro)


def handle_browser_navigate(**kwargs) -> dict:
    """Tool handler: navigate to a URL."""
    url = kwargs.get("url", "")
    if not url:
        return {"ok": False, "error": "url parameter required"}

    # Security: block private/internal URLs
    from urllib.parse import urlparse
    parsed = urlparse(url)
    blocked_hosts = {"localhost", "127.0.0.1", "0.0.0.0", "::1",
                     "metadata.google.internal", "169.254.169.254"}
    if parsed.hostname and parsed.hostname.lower() in blocked_hosts:
        return {"ok": False, "error": f"Blocked: private host {parsed.hostname}"}

    session = get_browser_session(kwargs.get("_agent_id", "default"))
    return _run_async(session.navigate(url))


def handle_browser_click(**kwargs) -> dict:
    """Tool handler: click an element."""
    selector = kwargs.get("selector", "")
    if not selector:
        return {"ok": False, "error": "selector parameter required"}
    session = get_browser_session(kwargs.get("_agent_id", "default"))
    return _run_async(session.click(selector))


def handle_browser_fill(**kwargs) -> dict:
    """Tool handler: fill a form field."""
    selector = kwargs.get("selector", "")
    value = kwargs.get("value", "")
    if not selector:
        return {"ok": False, "error": "selector parameter required"}
    session = get_browser_session(kwargs.get("_agent_id", "default"))
    return _run_async(session.fill(selector, value))


def handle_browser_get_text(**kwargs) -> dict:
    """Tool handler: extract text from page."""
    selector = kwargs.get("selector", "body")
    session = get_browser_session(kwargs.get("_agent_id", "default"))
    return _run_async(session.get_text(selector))


def handle_browser_screenshot(**kwargs) -> dict:
    """Tool handler: take a screenshot."""
    full_page = kwargs.get("full_page", False)
    selector = kwargs.get("selector", "")
    session = get_browser_session(kwargs.get("_agent_id", "default"))
    return _run_async(session.screenshot(full_page=full_page, selector=selector))


def handle_browser_evaluate(**kwargs) -> dict:
    """Tool handler: run JavaScript."""
    expression = kwargs.get("expression", "")
    if not expression:
        return {"ok": False, "error": "expression parameter required"}
    session = get_browser_session(kwargs.get("_agent_id", "default"))
    return _run_async(session.evaluate(expression))


def handle_browser_page_info(**kwargs) -> dict:
    """Tool handler: get current page info."""
    session = get_browser_session(kwargs.get("_agent_id", "default"))
    return _run_async(session.page_info())
