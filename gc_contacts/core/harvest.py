"""
Shared localized page harvesting helpers.
"""

from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass, field
from typing import Any, Optional

import gc_contacts.config as config
from gc_contacts.core.acquisition import acquire_page_content, try_render_page_html
from gc_contacts.core.discovery import gather_candidates_bundle
from gc_contacts.core.extraction import run_contact_extraction_pipeline
from gc_contacts.core.filtering import keep_contact
from gc_contacts.core.http_client import fetch_page, normalize_url
from gc_contacts.core.llm import gpt_clean_name, gpt_extract
from gc_contacts.core.utils import home_domain_of, tokens_of


EMAIL_LIKE_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", re.I)


@dataclass
class FetchedExtraction:
    url: str
    extraction_result: dict[str, Any]
    text: str
    page_length: int
    mailto_count: int
    visible_text_length: int = 0
    embedded_text_length: int = 0
    acquisition_modes: list[str] = field(default_factory=list)
    shell_like: bool = False
    embedded_document_count: int = 0
    primary_content_signature: str = ""
    content_signatures: list[str] = field(default_factory=list)
    zero_evidence_shell: bool = False
    weak_llm_shell_inference: bool = False
    short_circuited_shell: bool = False
    pages_fetched: int = 0
    tokens_in_estimate: int = 0
    tokens_out_estimate: int = 0


@dataclass
class ProbeSummary:
    probe_attempts: int = 0
    candidates_probed: int = 0
    contacts_extracted: int = 0
    best_url: str = ""
    best_url_kept: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    kept_contacts: list[dict[str, Any]] = field(default_factory=list)
    page_results: list[dict[str, Any]] = field(default_factory=list)
    missing_email_candidates: list[dict[str, Any]] = field(default_factory=list)
    rejected_contacts: list[dict[str, Any]] = field(default_factory=list)
    deduped_contacts: list[dict[str, Any]] = field(default_factory=list)
    source_breakdown: dict[str, int] = field(default_factory=dict)
    failed_fetches: int = 0
    pruned_candidates: list[dict[str, Any]] = field(default_factory=list)
    dead_shell_contexts: set[str] = field(default_factory=set)
    repeated_zero_evidence_signatures: dict[str, dict[str, Any]] = field(default_factory=dict)


@dataclass
class DirectCrawlResult:
    homepage_url: str
    elapsed_seconds: float
    candidates: list[dict[str, Any]] = field(default_factory=list)
    probe_summary: ProbeSummary = field(default_factory=ProbeSummary)
    discovery_by_strategy: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    discovery_candidate_counts: dict[str, int] = field(default_factory=dict)
    collector_breakdown: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    selected_mode: str = "hybrid"
    cms_wp: bool = False
    cms_drupal: bool = False
    hreflang_hopped: bool = False


def empty_extraction_result() -> dict[str, Any]:
    return {
        "raw_evidence": [],
        "assembled_candidates": [],
        "cleaned_candidates": [],
        "typed_candidates": [],
        "candidates_for_filtering": [],
        "named_contacts": [],
        "office_contacts": [],
        "missing_email_candidates": [],
        "junk_candidates": [],
        "raw_evidence_count_by_strategy": {
            "mailto_explicit": 0,
            "visible_regex": 0,
            "html_attribute": 0,
            "embedded_json": 0,
            "js_decode": 0,
            "explicit_obfuscation": 0,
            "llm_structured": 0,
        },
        "assembled_candidate_count": 0,
        "clean_candidate_count": 0,
        "named_contact_count": 0,
        "office_contact_count": 0,
        "person_without_email_count": 0,
        "junk_candidate_count": 0,
    }


def merge_extraction_results(results: list[dict[str, Any]]) -> dict[str, Any]:
    merged = empty_extraction_result()
    list_fields = (
        "raw_evidence",
        "assembled_candidates",
        "cleaned_candidates",
        "typed_candidates",
        "candidates_for_filtering",
        "named_contacts",
        "office_contacts",
        "missing_email_candidates",
        "junk_candidates",
    )
    count_fields = (
        "assembled_candidate_count",
        "clean_candidate_count",
        "named_contact_count",
        "office_contact_count",
        "person_without_email_count",
        "junk_candidate_count",
    )

    for result in results:
        if not result:
            continue
        for field in list_fields:
            merged[field].extend(result.get(field, []) or [])
        for field in count_fields:
            merged[field] += int(result.get(field, 0) or 0)
        for strategy, count in (result.get("raw_evidence_count_by_strategy") or {}).items():
            merged["raw_evidence_count_by_strategy"][strategy] = (
                merged["raw_evidence_count_by_strategy"].get(strategy, 0) + int(count or 0)
            )
    return merged


def _page_text_signature(text: str) -> str:
    normalized = re.sub(r"\s+", " ", str(text or "").strip().lower())
    if not normalized:
        return ""
    return hashlib.sha1(normalized[:2500].encode("utf-8", "ignore")).hexdigest()


def _has_email_like_signal(*, html: str, visible_text: str) -> bool:
    html_l = str(html or "").lower()
    visible = str(visible_text or "")
    return "mailto:" in html_l or bool(EMAIL_LIKE_RE.search(html or "")) or bool(EMAIL_LIKE_RE.search(visible))


def is_weak_llm_shell_inference(extraction_result: dict[str, Any]) -> bool:
    source_breakdown = extraction_result.get("raw_evidence_count_by_strategy") or {}
    llm_count = int(source_breakdown.get("llm_structured", 0) or 0)
    non_llm_count = sum(
        int(count or 0)
        for strategy, count in source_breakdown.items()
        if str(strategy or "").strip().lower() != "llm_structured"
    )
    if llm_count <= 0 or non_llm_count > 0:
        return False

    candidates_for_filtering = extraction_result.get("candidates_for_filtering", []) or []
    if candidates_for_filtering:
        return False

    raw_evidence = extraction_result.get("raw_evidence", []) or []
    typed_candidates = extraction_result.get("typed_candidates", []) or []
    missing_email_candidates = extraction_result.get("missing_email_candidates", []) or []
    junk_candidates = extraction_result.get("junk_candidates", []) or []
    if not raw_evidence and not typed_candidates and not missing_email_candidates and not junk_candidates:
        return False

    for item in [*raw_evidence, *typed_candidates, *missing_email_candidates, *junk_candidates]:
        email = str(item.get("email_normalized") or item.get("email") or "").strip().lower()
        if email:
            return False
        strategies = {
            str(strategy or "").strip().lower()
            for strategy in (item.get("source_strategies", []) or [])
            if str(strategy or "").strip()
        }
        if strategies and strategies != {"llm_structured"}:
            return False

    allowed_candidate_types = {"", "person_without_email", "junk_candidate"}
    for item in typed_candidates:
        candidate_type = str(item.get("candidate_type", "") or "").strip()
        if candidate_type not in allowed_candidate_types:
            return False

    return True


def looks_like_zero_evidence_shell(
    payload: dict[str, Any],
    *,
    shell_like: bool,
    text: str,
) -> bool:
    text_length = int(payload.get("text_length", 0) or 0)
    if text_length <= 0:
        text_length = len(str(text or "").strip())
    if not bool(shell_like) or text_length < 1000:
        return False
    if (
        int(payload.get("assembled_candidate_count", 0) or 0) <= 0
        and len(payload.get("raw_evidence", []) or []) <= 0
        and len(payload.get("missing_email_candidates", []) or []) <= 0
    ):
        return True
    return is_weak_llm_shell_inference(payload)


def _should_short_circuit_shell(
    *,
    html: str,
    visible_text: str,
    embedded_text: str,
    shell_like: bool,
    embedded_document_count: int,
) -> bool:
    if not shell_like:
        return False
    if embedded_document_count > 0 or str(embedded_text or "").strip():
        return False
    if _has_email_like_signal(html=html, visible_text=visible_text):
        return False
    return len(str(visible_text or "").strip()) >= 1000


def _candidate_shell_context_key(candidate: dict[str, Any], home_url: str) -> str:
    source_strategy = str(candidate.get("source_strategy", "") or "").strip().lower()
    source_stage = str(candidate.get("source_stage", "") or "").strip().lower()
    parent_url = str(candidate.get("parent_url", "") or "").strip() or str(home_url or "").strip()
    if not source_strategy and not source_stage and not parent_url:
        return ""
    return "|".join((source_strategy, source_stage, parent_url))


def find_pagination_links(html: str, base_url: str) -> list[str]:
    """Find next-page links in HTML."""
    from bs4 import BeautifulSoup, FeatureNotFound
    from urllib.parse import urljoin
    import re

    try:
        soup = BeautifulSoup(html, "lxml")
    except FeatureNotFound:
        soup = BeautifulSoup(html, "html.parser")

    links = set()
    for a in soup.find_all("a", rel=lambda value: value and "next" in value):
        href = a.get("href")
        if href:
            links.add(urljoin(base_url, href))
    for a in soup.find_all("a", href=True):
        cls = " ".join(a.get("class", []))
        if any(keyword in cls.lower() for keyword in ["pager", "pagination", "nav", "next"]):
            links.add(urljoin(base_url, a["href"]))
        href = a["href"]
        if re.search(r"[?&]page=\d+", href) or re.search(r"/page/\d+/?", href):
            links.add(urljoin(base_url, href))

    return list({normalize_url(url) for url in links})


async def _extract_page_html(
    url: str,
    html: str,
    *,
    role_keywords: Optional[list[str]] = None,
    country: Optional[str] = None,
    allow_generic_emails: bool = False,
    use_llm: bool = True,
    llm_name_cleaner=None,
) -> tuple[dict[str, Any], str, int, int, int, int, int, int, str, bool, int, bool]:
    acquired = acquire_page_content(html)
    text = acquired.effective_text
    short_circuited_shell = _should_short_circuit_shell(
        html=html,
        visible_text=acquired.visible_text,
        embedded_text=acquired.embedded_text,
        shell_like=acquired.shell_like,
        embedded_document_count=acquired.embedded_document_count,
    )
    if short_circuited_shell:
        return (
            empty_extraction_result(),
            text,
            len(html),
            html.lower().count("mailto:"),
            0,
            0,
            len(acquired.visible_text),
            len(acquired.embedded_text),
            acquired.acquisition_mode,
            acquired.shell_like,
            acquired.embedded_document_count,
            True,
        )
    llm_extractor = gpt_extract if use_llm else None
    llm_cleaner = llm_name_cleaner if use_llm else None
    extraction_result = await run_contact_extraction_pipeline(
        html,
        text,
        url,
        role_keywords=role_keywords,
        country=country,
        allow_generic_emails=allow_generic_emails,
        llm_extractor=llm_extractor,
        llm_name_cleaner=llm_cleaner,
    )
    tokens_in = tokens_of(text[:8000]) + 200 if use_llm and text else 0
    tokens_out = 600 if use_llm and text else 0
    return (
        extraction_result,
        text,
        len(html),
        html.lower().count("mailto:"),
        tokens_in,
        tokens_out,
        len(acquired.visible_text),
        len(acquired.embedded_text),
        acquired.acquisition_mode,
        acquired.shell_like,
        acquired.embedded_document_count,
        False,
    )


async def _extract_with_render_fallback(
    url: str,
    html: str,
    *,
    role_keywords: Optional[list[str]] = None,
    country: Optional[str] = None,
    allow_generic_emails: bool = False,
    use_llm: bool = True,
    llm_name_cleaner=None,
) -> tuple[dict[str, Any], str, int, int, int, int, int, int, str, bool, int, bool]:
    static_result = await _extract_page_html(
        url,
        html,
        role_keywords=role_keywords,
        country=country,
        allow_generic_emails=allow_generic_emails,
        use_llm=use_llm,
        llm_name_cleaner=llm_name_cleaner,
    )
    (
        extraction_result,
        text,
        page_length,
        mailto_count,
        tokens_in,
        tokens_out,
        visible_text_length,
        embedded_text_length,
        acquisition_mode,
        shell_like,
        embedded_document_count,
        short_circuited_shell,
    ) = static_result

    zero_evidence_shell = looks_like_zero_evidence_shell(extraction_result, shell_like=shell_like, text=text)
    should_try_render = (
        bool(getattr(config, "RENDER_FALLBACK_ENABLED", True))
        and bool(shell_like)
        and embedded_document_count <= 0
        and (short_circuited_shell or zero_evidence_shell or len(str(text or "").strip()) <= 250)
        and not str(acquisition_mode or "").startswith("rendered_")
    )
    if not should_try_render:
        return static_result

    rendered_html = await try_render_page_html(
        url,
        timeout_ms=int(getattr(config, "RENDER_FALLBACK_TIMEOUT_MS", 8000) or 8000),
        post_load_wait_ms=int(getattr(config, "RENDER_FALLBACK_WAIT_MS", 800) or 800),
    )
    if not rendered_html or rendered_html == html:
        return static_result

    rendered_result = await _extract_page_html(
        url,
        rendered_html,
        role_keywords=role_keywords,
        country=country,
        allow_generic_emails=allow_generic_emails,
        use_llm=use_llm,
        llm_name_cleaner=llm_name_cleaner,
    )
    rendered_mode = str(rendered_result[8] or "")
    rendered_result = (
        rendered_result[0],
        rendered_result[1],
        rendered_result[2],
        rendered_result[3],
        rendered_result[4],
        rendered_result[5],
        rendered_result[6],
        rendered_result[7],
        f"rendered_{rendered_mode}" if rendered_mode else "rendered_dom",
        rendered_result[9],
        rendered_result[10],
        rendered_result[11],
    )

    rendered_zero_evidence_shell = looks_like_zero_evidence_shell(
        rendered_result[0],
        shell_like=bool(rendered_result[9]),
        text=str(rendered_result[1] or ""),
    )
    rendered_has_signal = (
        int(rendered_result[0].get("assembled_candidate_count", 0) or 0) > 0
        or len(rendered_result[0].get("candidates_for_filtering", []) or []) > 0
        or len(rendered_result[0].get("missing_email_candidates", []) or []) > 0
    )
    if rendered_zero_evidence_shell and not rendered_has_signal:
        return static_result
    return rendered_result


async def fetch_and_extract_contacts(
    url: str,
    *,
    role_keywords: Optional[list[str]] = None,
    country: Optional[str] = None,
    allow_generic_emails: bool = False,
    use_llm: bool = True,
    include_pagination: bool = False,
    pagination_cap: Optional[int] = None,
    llm_name_cleaner=None,
) -> FetchedExtraction:
    """
    Fetch a page and run the staged extraction pipeline, optionally following
    pagination links with the same shared extraction logic.
    """
    html = await fetch_page(url)
    if not html:
        return FetchedExtraction(
            url=url,
            extraction_result=empty_extraction_result(),
            text="",
            page_length=0,
            mailto_count=0,
        )

    (
        extraction_result,
        text,
        page_length,
        mailto_count,
        tokens_in,
        tokens_out,
        visible_text_length,
        embedded_text_length,
        acquisition_mode,
        shell_like,
        embedded_document_count,
        short_circuited_shell,
    ) = await _extract_with_render_fallback(
        url,
        html,
        role_keywords=role_keywords,
        country=country,
        allow_generic_emails=allow_generic_emails,
        use_llm=use_llm,
        llm_name_cleaner=llm_name_cleaner,
    )
    results = [extraction_result]
    texts = [text]
    pages_fetched = 1
    acquisition_modes = [acquisition_mode]
    any_shell_like = shell_like
    total_visible_text_length = visible_text_length
    total_embedded_text_length = embedded_text_length
    total_embedded_document_count = embedded_document_count
    content_signatures = [_page_text_signature(text)] if text else []
    weak_llm_shell_inference = is_weak_llm_shell_inference(extraction_result)
    zero_evidence_shell = looks_like_zero_evidence_shell(extraction_result, shell_like=shell_like, text=text)
    any_short_circuited_shell = short_circuited_shell

    if include_pagination and not zero_evidence_shell:
        next_links = find_pagination_links(html, url)[: pagination_cap or config.PAGINATION_CAP]
        for next_url in next_links:
            next_html = await fetch_page(next_url)
            if not next_html:
                continue
            (
                next_result,
                next_text,
                next_len,
                next_mailtos,
                next_tokens_in,
                next_tokens_out,
                next_visible_text_length,
                next_embedded_text_length,
                next_acquisition_mode,
                next_shell_like,
                next_embedded_document_count,
                next_short_circuited_shell,
            ) = await _extract_page_html(
                next_url,
                next_html,
                role_keywords=role_keywords,
                country=country,
                allow_generic_emails=allow_generic_emails,
                use_llm=use_llm,
                llm_name_cleaner=llm_name_cleaner,
            )
            results.append(next_result)
            texts.append(next_text)
            page_length += next_len
            mailto_count += next_mailtos
            tokens_in += next_tokens_in
            tokens_out += next_tokens_out
            total_visible_text_length += next_visible_text_length
            total_embedded_text_length += next_embedded_text_length
            total_embedded_document_count += next_embedded_document_count
            any_shell_like = any_shell_like or next_shell_like
            any_short_circuited_shell = any_short_circuited_shell or next_short_circuited_shell
            if next_acquisition_mode not in acquisition_modes:
                acquisition_modes.append(next_acquisition_mode)
            next_signature = _page_text_signature(next_text)
            if next_signature and next_signature not in content_signatures:
                content_signatures.append(next_signature)
            pages_fetched += 1

    return FetchedExtraction(
        url=url,
        extraction_result=merge_extraction_results(results),
        text="\n".join(part for part in texts if part),
        page_length=page_length,
        mailto_count=mailto_count,
        visible_text_length=total_visible_text_length,
        embedded_text_length=total_embedded_text_length,
        acquisition_modes=acquisition_modes,
        shell_like=any_shell_like,
        embedded_document_count=total_embedded_document_count,
        primary_content_signature=content_signatures[0] if content_signatures else "",
        content_signatures=content_signatures,
        zero_evidence_shell=zero_evidence_shell,
        weak_llm_shell_inference=weak_llm_shell_inference,
        short_circuited_shell=any_short_circuited_shell,
        pages_fetched=pages_fetched,
        tokens_in_estimate=tokens_in,
        tokens_out_estimate=tokens_out,
    )


def evaluate_extracted_contacts(
    extraction_result: dict[str, Any],
    home_domain: str,
    *,
    country: Optional[str] = None,
    min_score: Optional[int] = None,
    allow_generic: bool = False,
    extra_positive: Optional[list[str]] = None,
    extra_negative: Optional[list[str]] = None,
    seen_emails: Optional[set[str]] = None,
) -> dict[str, list[dict[str, Any]]]:
    kept_contacts: list[dict[str, Any]] = []
    rejected_contacts: list[dict[str, Any]] = []
    deduped_contacts: list[dict[str, Any]] = []
    seen_emails = seen_emails if seen_emails is not None else set()

    for contact in extraction_result.get("candidates_for_filtering", []):
        email = str(contact.get("email", "") or "").lower().strip()
        if not email:
            continue
        if email in seen_emails:
            deduped_contacts.append(
                {
                    "email": email,
                    "page_url": contact.get("page_url", ""),
                    "reason": "already seen for target",
                }
            )
            continue

        seen_emails.add(email)
        keep, score, reason = keep_contact(
            contact,
            home_domain,
            min_score=min_score,
            allow_generic=allow_generic,
            extra_positive=extra_positive,
            extra_negative=extra_negative,
            country=country,
        )
        record = dict(contact)
        record["score"] = score
        record["reason"] = reason
        if keep:
            kept_contacts.append(record)
        else:
            rejected_contacts.append(record)

    return {
        "kept_contacts": kept_contacts,
        "rejected_contacts": rejected_contacts,
        "deduped_contacts": deduped_contacts,
        "missing_email_candidates": list(extraction_result.get("missing_email_candidates", []) or []),
    }


async def probe_candidate_pages(
    candidates: list[dict[str, Any]],
    home_url: str,
    *,
    max_to_probe: int = 10,
    role_keywords: Optional[list[str]] = None,
    country: Optional[str] = None,
    min_score: Optional[int] = None,
    allow_generic: bool = False,
    allow_generic_emails: bool = False,
    extra_positive: Optional[list[str]] = None,
    extra_negative: Optional[list[str]] = None,
    use_llm: bool = True,
    include_pagination: bool = True,
    llm_name_cleaner=None,
) -> ProbeSummary:
    summary = ProbeSummary()
    home_domain = home_domain_of(home_url)
    seen_emails: set[str] = set()
    for candidate in candidates:
        if summary.probe_attempts >= max_to_probe:
            break
        shell_context_key = _candidate_shell_context_key(candidate, home_url)
        if shell_context_key and shell_context_key in summary.dead_shell_contexts:
            summary.pruned_candidates.append(
                {
                    "url": str(candidate.get("url", "")),
                    "reason": "dead_shell_context",
                    "details": {"shell_context_key": shell_context_key},
                }
            )
            continue

        fetched = await fetch_and_extract_contacts(
            candidate["url"],
            role_keywords=role_keywords,
            country=country,
            allow_generic_emails=allow_generic_emails,
            use_llm=use_llm,
            include_pagination=include_pagination,
            llm_name_cleaner=llm_name_cleaner,
        )
        extraction_result = fetched.extraction_result
        summary.probe_attempts += 1
        if fetched.pages_fetched > 0:
            summary.candidates_probed += 1
        else:
            summary.failed_fetches += 1

        evaluated = evaluate_extracted_contacts(
            extraction_result,
            home_domain,
            country=country,
            min_score=min_score,
            allow_generic=allow_generic,
            extra_positive=extra_positive,
            extra_negative=extra_negative,
            seen_emails=seen_emails,
        )
        page_kept = evaluated["kept_contacts"]

        summary.contacts_extracted += int(extraction_result.get("assembled_candidate_count", 0) or 0)
        summary.tokens_in += fetched.tokens_in_estimate
        summary.tokens_out += fetched.tokens_out_estimate
        summary.kept_contacts.extend(page_kept)
        summary.missing_email_candidates.extend(evaluated["missing_email_candidates"])
        summary.rejected_contacts.extend(evaluated["rejected_contacts"])
        summary.deduped_contacts.extend(evaluated["deduped_contacts"])

        for strategy, count in (extraction_result.get("raw_evidence_count_by_strategy") or {}).items():
            summary.source_breakdown[strategy] = summary.source_breakdown.get(strategy, 0) + int(count or 0)

        if len(page_kept) > summary.best_url_kept:
            summary.best_url = fetched.url
            summary.best_url_kept = len(page_kept)

        duplicate_of = ""
        signature_key = fetched.primary_content_signature
        if fetched.zero_evidence_shell and signature_key:
            signature_state = summary.repeated_zero_evidence_signatures.setdefault(
                signature_key,
                {"urls": [], "shell_contexts": [], "count": 0},
            )
            if signature_state["urls"]:
                duplicate_of = str(signature_state["urls"][0])
            if fetched.url not in signature_state["urls"]:
                signature_state["urls"].append(fetched.url)
            if shell_context_key and shell_context_key not in signature_state["shell_contexts"]:
                signature_state["shell_contexts"].append(shell_context_key)
            signature_state["count"] = len(signature_state["urls"])
            if signature_state["count"] >= 2 and shell_context_key:
                summary.dead_shell_contexts.add(shell_context_key)
                summary.pruned_candidates.append(
                    {
                        "url": fetched.url,
                        "reason": "repeated_zero_evidence_shell_content",
                        "details": {
                            "content_signature": signature_key,
                            "duplicate_of": duplicate_of or fetched.url,
                            "shell_context_key": shell_context_key,
                            "repeat_count": signature_state["count"],
                        },
                    }
                )

        summary.page_results.append(
            {
                "candidate": candidate,
                "url": fetched.url,
                "raw_contacts": int(extraction_result.get("assembled_candidate_count", 0) or 0),
                "kept_contacts": page_kept,
                "rejected_contacts": evaluated["rejected_contacts"],
                "missing_email_candidates": evaluated["missing_email_candidates"],
                "page_length": fetched.page_length,
                "mailto_count": fetched.mailto_count,
                "visible_text_length": fetched.visible_text_length,
                "embedded_text_length": fetched.embedded_text_length,
                "embedded_document_count": fetched.embedded_document_count,
                "acquisition_modes": list(fetched.acquisition_modes),
                "shell_like": fetched.shell_like,
                "content_signature": fetched.primary_content_signature,
                "content_signatures": list(fetched.content_signatures),
                "zero_evidence_shell": fetched.zero_evidence_shell,
                "weak_llm_shell_inference": fetched.weak_llm_shell_inference,
                "short_circuited_shell": fetched.short_circuited_shell,
                "duplicate_of": duplicate_of,
                "shell_context_key": shell_context_key,
                "pages_fetched": fetched.pages_fetched,
                "fetch_succeeded": bool(fetched.pages_fetched),
                "source_breakdown": dict(extraction_result.get("raw_evidence_count_by_strategy") or {}),
            }
        )

    return summary


async def crawl_target_direct(
    home_url: str,
    *,
    target_name: Optional[str] = None,
    country: Optional[str] = None,
    discovery_mode: str = "heuristic_only",
    extra_slugs: Optional[list[str]] = None,
    role_keywords: Optional[list[str]] = None,
    min_score: Optional[int] = None,
    allow_generic: bool = False,
    allow_generic_emails: bool = False,
    extra_positive: Optional[list[str]] = None,
    extra_negative: Optional[list[str]] = None,
    use_llm: bool = True,
    max_candidates_to_probe: int = 10,
    include_strategy_breakdown: bool = False,
    include_pagination: bool = True,
    llm_name_cleaner=None,
) -> DirectCrawlResult:
    start = time.time()
    bundle = await gather_candidates_bundle(
        home_url,
        target_name=target_name,
        country=country,
        extra_slugs=extra_slugs,
        mode=discovery_mode,
        include_strategy_breakdown=include_strategy_breakdown,
    )
    candidates = list(bundle.get("candidates", []) or [])
    resolved_home_url = str(bundle.get("resolved_home_url", "") or "").strip() or home_url
    if not candidates:
        return DirectCrawlResult(
            homepage_url=resolved_home_url,
            elapsed_seconds=time.time() - start,
            candidates=[],
            discovery_by_strategy=bundle.get("by_strategy", {}) or {},
            discovery_candidate_counts=bundle.get("by_strategy_candidate_counts", {}) or {},
            collector_breakdown=bundle.get("collector_breakdown", {}) or {},
            selected_mode=discovery_mode,
            cms_wp=bool(bundle.get("cms_wp")),
            cms_drupal=bool(bundle.get("cms_drupal")),
            hreflang_hopped=bool(bundle.get("hreflang_hopped")),
        )

    probe_summary = await probe_candidate_pages(
        candidates,
        resolved_home_url,
        max_to_probe=max_candidates_to_probe,
        role_keywords=role_keywords,
        country=country,
        min_score=min_score,
        allow_generic=allow_generic,
        allow_generic_emails=allow_generic_emails,
        extra_positive=extra_positive,
        extra_negative=extra_negative,
        use_llm=use_llm,
        include_pagination=include_pagination,
        llm_name_cleaner=llm_name_cleaner if llm_name_cleaner is not None else gpt_clean_name,
    )
    return DirectCrawlResult(
        homepage_url=resolved_home_url,
        elapsed_seconds=time.time() - start,
        candidates=candidates,
        probe_summary=probe_summary,
        discovery_by_strategy=bundle.get("by_strategy", {}) or {},
        discovery_candidate_counts=bundle.get("by_strategy_candidate_counts", {}) or {},
        collector_breakdown=bundle.get("collector_breakdown", {}) or {},
        selected_mode=discovery_mode,
        cms_wp=bool(bundle.get("cms_wp")),
        cms_drupal=bool(bundle.get("cms_drupal")),
        hreflang_hopped=bool(bundle.get("hreflang_hopped")),
    )
