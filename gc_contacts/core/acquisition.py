"""
Page acquisition helpers for static HTML plus embedded app-state fallbacks.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from typing import Any

from bs4 import BeautifulSoup, FeatureNotFound

from gc_contacts.core.http_client import bs_text

JSON_ASSIGNMENT_MARKERS = (
    "__NEXT_DATA__",
    "__NUXT__",
    "__INITIAL_STATE__",
    "__INITIAL_PROPS__",
    "__APOLLO_STATE__",
    "window.__INITIAL_STATE__",
    "window.__PRELOADED_STATE__",
    "window.__NUXT__",
    "window.__NEXT_DATA__",
    "window.__APOLLO_STATE__",
    "drupalSettings",
)

CONTACT_KEY_TERMS = (
    "email",
    "mail",
    "contact",
    "person",
    "people",
    "staff",
    "office",
    "role",
    "title",
    "name",
    "department",
    "unit",
    "team",
)

CONTACT_VALUE_TERMS = (
    "@",
    "international",
    "partnership",
    "mobility",
    "exchange",
    "erasmus",
    "global",
    "contact",
    "office",
    "staff",
    "directory",
    "rector",
    "president",
    "director",
)

MAX_EMBEDDED_DOCS = 16
MAX_EMBEDDED_LINES = 320
MAX_EMBEDDED_TEXT_CHARS = 18000


@dataclass
class AcquiredPageContent:
    visible_text: str
    embedded_text: str
    effective_text: str
    acquisition_mode: str
    shell_like: bool
    embedded_document_count: int


def _normalize_space(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _is_relevant_key(key: str) -> bool:
    lowered = _normalize_space(key).lower()
    return any(term in lowered for term in CONTACT_KEY_TERMS)


def _is_relevant_value(value: str) -> bool:
    lowered = _normalize_space(value).lower()
    return any(term in lowered for term in CONTACT_VALUE_TERMS)


def _coerce_json_document(script_text: str) -> Any | None:
    payload = str(script_text or "").strip().strip(";")
    if not payload or payload[:1] not in "{[":
        return None
    try:
        return json.loads(payload)
    except Exception:
        return None


def _balanced_json_segment(text: str, start_index: int) -> str:
    open_char = text[start_index]
    close_char = "}" if open_char == "{" else "]"
    depth = 0
    in_string = False
    escaped = False

    for index in range(start_index, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
            continue
        if char == open_char:
            depth += 1
            continue
        if char == close_char:
            depth -= 1
            if depth == 0:
                return text[start_index : index + 1]
    return ""


def _extract_assignment_documents(script_text: str) -> list[Any]:
    documents: list[Any] = []
    lowered = script_text.lower()

    for marker in JSON_ASSIGNMENT_MARKERS:
        start = 0
        marker_l = marker.lower()
        while len(documents) < MAX_EMBEDDED_DOCS:
            position = lowered.find(marker_l, start)
            if position < 0:
                break
            brace_candidates = [
                index
                for index in (script_text.find("{", position), script_text.find("[", position))
                if index >= 0
            ]
            if not brace_candidates:
                break
            json_start = min(brace_candidates)
            snippet = _balanced_json_segment(script_text, json_start)
            if not snippet:
                break
            try:
                documents.append(json.loads(snippet))
            except Exception:
                pass
            start = json_start + len(snippet)

    return documents


def iter_embedded_json_documents(html: str) -> list[Any]:
    try:
        soup = BeautifulSoup(html, "lxml")
    except FeatureNotFound:
        soup = BeautifulSoup(html, "html.parser")

    documents: list[Any] = []
    for script in soup.find_all("script"):
        script_text = script.string if isinstance(script.string, str) else script.get_text(" ", strip=False)
        script_text = str(script_text or "").strip()
        if len(script_text) < 20:
            continue

        script_type = str(script.get("type", "") or "").strip().lower()
        if "json" in script_type:
            document = _coerce_json_document(script_text)
            if document is not None:
                documents.append(document)
        if len(documents) >= MAX_EMBEDDED_DOCS:
            break

        if any(marker.lower() in script_text.lower() for marker in JSON_ASSIGNMENT_MARKERS):
            documents.extend(_extract_assignment_documents(script_text))
        if len(documents) >= MAX_EMBEDDED_DOCS:
            break

    return documents[:MAX_EMBEDDED_DOCS]


def _collect_embedded_strings(node: Any, path: tuple[str, ...], lines: list[str]) -> None:
    if len(lines) >= MAX_EMBEDDED_LINES:
        return
    if isinstance(node, dict):
        for key, value in node.items():
            _collect_embedded_strings(value, path + (_normalize_space(key).lower(),), lines)
            if len(lines) >= MAX_EMBEDDED_LINES:
                return
        return
    if isinstance(node, list):
        for item in node:
            _collect_embedded_strings(item, path, lines)
            if len(lines) >= MAX_EMBEDDED_LINES:
                return
        return
    if not isinstance(node, str):
        return

    text = _normalize_space(node)
    if not text or len(text) > 280:
        return
    if not (_is_relevant_value(text) or any(_is_relevant_key(segment) for segment in path)):
        return

    path_hint = " > ".join(segment for segment in path[-3:] if segment)
    lines.append(f"{path_hint}: {text}" if path_hint else text)


def extract_embedded_data_text(html: str) -> tuple[str, int]:
    lines: list[str] = []
    documents = iter_embedded_json_documents(html)
    for document in documents:
        _collect_embedded_strings(document, (), lines)
        if len(lines) >= MAX_EMBEDDED_LINES:
            break

    deduped: list[str] = []
    seen: set[str] = set()
    total_chars = 0
    for line in lines:
        normalized = _normalize_space(line)
        if not normalized or normalized in seen:
            continue
        projected = total_chars + len(normalized) + 1
        if projected > MAX_EMBEDDED_TEXT_CHARS:
            break
        deduped.append(normalized)
        seen.add(normalized)
        total_chars = projected

    return "\n".join(deduped), len(documents)


def _script_count(html: str) -> int:
    return html.lower().count("<script")


def detect_shell_like_html(html: str, visible_text: str, embedded_text: str = "", embedded_document_count: int = 0) -> bool:
    html_length = len(html or "")
    visible_length = len(visible_text or "")
    script_count = _script_count(html or "")
    text_density = visible_length / max(html_length, 1)

    if html_length >= 180000 and script_count >= 12 and text_density <= 0.06:
        return True
    if html_length >= 90000 and embedded_document_count >= 1 and visible_length <= 1200:
        return True
    if html_length >= 90000 and embedded_text and len(embedded_text) >= max(800, visible_length * 2):
        return True
    return False


def _merge_text_layers(visible_text: str, embedded_text: str) -> str:
    visible = _normalize_space(visible_text)
    embedded = _normalize_space(embedded_text)
    if not embedded:
        return visible
    if not visible:
        return embedded
    if embedded in visible:
        return visible
    return f"{visible}\n\n{embedded}"


def acquire_page_content(html: str) -> AcquiredPageContent:
    visible_text = bs_text(html)
    embedded_text, embedded_document_count = extract_embedded_data_text(html)
    shell_like = detect_shell_like_html(
        html,
        visible_text,
        embedded_text=embedded_text,
        embedded_document_count=embedded_document_count,
    )
    effective_text = _merge_text_layers(visible_text, embedded_text)
    acquisition_mode = "embedded_app_state_overlay" if embedded_text and shell_like else "static_html"
    if embedded_text and not shell_like:
        acquisition_mode = "static_html_plus_embedded_hints"
    return AcquiredPageContent(
        visible_text=visible_text,
        embedded_text=embedded_text,
        effective_text=effective_text,
        acquisition_mode=acquisition_mode,
        shell_like=shell_like,
        embedded_document_count=embedded_document_count,
    )


async def try_render_page_html(
    url: str,
    *,
    timeout_ms: int = 8000,
    post_load_wait_ms: int = 800,
) -> str | None:
    """
    Best-effort rendered HTML fallback.

    This is optional by design: if Playwright is unavailable, the fallback is a
    no-op so local/static runs still work.
    """
    try:
        from playwright.async_api import TimeoutError as PlaywrightTimeoutError
        from playwright.async_api import async_playwright
    except ModuleNotFoundError:
        return None

    blocked_resource_types = {"font", "image", "media", "stylesheet"}
    blocked_extensions = (".css", ".gif", ".ico", ".jpeg", ".jpg", ".png", ".svg", ".webp", ".woff", ".woff2")
    browser = None
    page = None
    playwright = None

    try:
        playwright = await async_playwright().start()
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page()

        async def _route_handler(route) -> None:
            request = route.request
            resource_type = str(request.resource_type or "").strip().lower()
            request_url = str(request.url or "").strip().lower()
            if resource_type in blocked_resource_types or request_url.endswith(blocked_extensions):
                await route.abort()
                return
            await route.continue_()

        await page.route("**/*", _route_handler)
        await page.goto(url, wait_until="domcontentloaded", timeout=max(1000, int(timeout_ms or 0)))
        if post_load_wait_ms > 0:
            await page.wait_for_timeout(max(0, int(post_load_wait_ms)))
        return await page.content()
    except (PlaywrightTimeoutError, asyncio.TimeoutError, Exception):
        return None
    finally:
        if page is not None:
            try:
                await page.close()
            except Exception:
                pass
        if browser is not None:
            try:
                await browser.close()
            except Exception:
                pass
        if playwright is not None:
            try:
                await playwright.stop()
            except Exception:
                pass
