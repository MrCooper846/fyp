"""
Legacy direct crawl orchestration.
"""

from __future__ import annotations

import asyncio
import csv
import heapq
import itertools
import logging
import random
from typing import Dict, List, Optional, Set, Tuple

import pandas as pd
from tqdm.asyncio import tqdm

import gc_contacts.config as config
from gc_contacts.core.debug import append_training_row, write_debug_json
from gc_contacts.core.harvest import crawl_target_direct
from gc_contacts.core.llm import gpt_clean_name
from gc_contacts.core.models import Target
from gc_contacts.core.utils import cost_for_tokens, url_features
from gc_contacts.pipelines.shared import collect_targets, pipeline_runtime
from gc_contacts.sources.openalex_source import OpenAlexSource

LOG = logging.getLogger("gc")


def _contact_row(university: str, country: str, contact: dict) -> dict[str, str | int]:
    return {
        "University": university,
        "Country": country,
        "Role": str(contact.get("role", "")),
        "Name": str(contact.get("name", "")),
        "Email": str(contact.get("email", "")).lower(),
        "PageURL": str(contact.get("page_url", "")),
        "Score": int(contact.get("score", 0) or 0),
        "Reason": str(contact.get("reason", "")),
    }


async def process_uni(
    uni: Target,
    country: str,
    sem: asyncio.Semaphore,
    results: List[Dict[str, str]],
    seen: Set[Tuple[str, str]],
    stats: Dict[str, int],
    emit_all: bool,
    per_uni_max: Optional[int],
    verify_names: bool,
    discovery_mode: str,
):
    """Process a single university."""
    async with sem:
        home = uni.url.rstrip("/")
        LOG.info("Processing university: %s (%s)", uni.name, home)

        uni_pool: List[Tuple[int, int, Dict[str, str]]] = []
        tie_breaker = itertools.count()

        crawl = await crawl_target_direct(
            home,
            country=country,
            discovery_mode=discovery_mode,
            role_keywords=[],
            min_score=None,
            allow_generic=False,
            allow_generic_emails=False,
            extra_positive=None,
            extra_negative=None,
            use_llm=True,
            max_candidates_to_probe=config.PROBE_LIMIT,
            include_strategy_breakdown=config.DEBUG_ENABLED,
            include_pagination=True,
            llm_name_cleaner=gpt_clean_name if verify_names else None,
        )
        cands = crawl.candidates
        stats["tok_in"] += crawl.probe_summary.tokens_in
        stats["tok_out"] += crawl.probe_summary.tokens_out

        if not cands:
            LOG.info("No candidate pages for %s", uni.name)
            if config.DEBUG_ENABLED:
                await write_debug_json(
                    uni.name,
                    {
                        "university": uni.name,
                        "home_url": home,
                        "discovery_mode": discovery_mode,
                        "discovery_by_strategy": crawl.discovery_by_strategy,
                        "candidates_ranked": [],
                        "probed_pages": [],
                        "best_page": None,
                        "note": "no candidates",
                    },
                )
            return

        for page in crawl.probe_summary.page_results:
            for contact in page.get("kept_contacts", []):
                email = str(contact.get("email", "")).lower()
                if not email:
                    continue
                key = (uni.name, email)
                if key in seen:
                    continue
                seen.add(key)

                row = _contact_row(uni.name, country, contact)
                score = int(row["Score"])
                if per_uni_max and per_uni_max > 0:
                    entry = (score, next(tie_breaker), row)
                    if len(uni_pool) < per_uni_max:
                        heapq.heappush(uni_pool, entry)
                    elif score > uni_pool[0][0]:
                        heapq.heapreplace(uni_pool, entry)
                else:
                    results.append(row)

            if emit_all:
                for contact in page.get("rejected_contacts", []):
                    results.append(_contact_row(uni.name, country, contact))

            candidate = page.get("candidate", {})
            feats = url_features(page.get("url", ""))
            await append_training_row(
                {
                    "university": uni.name,
                    "country": country,
                    "homepage": home,
                    "candidate_url": page.get("url", ""),
                    "source_type": candidate.get("source_type", ""),
                    "source_strategy": candidate.get("source_strategy", ""),
                    "source_stage": candidate.get("source_stage", ""),
                    "page_family": candidate.get("page_family", ""),
                    "parent_url": candidate.get("parent_url", ""),
                    "anchor_text": candidate.get("anchor_text", ""),
                    "heuristic_score": candidate.get("heuristic_score", 0.0),
                    "raw_contacts": page.get("raw_contacts", 0),
                    "kept_contacts": len(page.get("kept_contacts", [])),
                    "page_length": page.get("page_length", 0),
                    "mailto_count": page.get("mailto_count", 0),
                    "depth": feats["depth"],
                    "path_tokens": feats["path_tokens"],
                    "subdomain": feats["subdomain"],
                    "ext": feats["ext"],
                    "cms_wordpress": int(crawl.cms_wp),
                    "cms_drupal": int(crawl.cms_drupal),
                    "hreflang_en_hop": int(crawl.hreflang_hopped),
                    "Label": "",
                    "ReasonCode": "",
                    "Notes": "",
                }
            )

        if config.DEBUG_ENABLED:
            best_page = None
            if crawl.probe_summary.best_url:
                best_page = {
                    "url": crawl.probe_summary.best_url,
                    "kept_contacts": crawl.probe_summary.best_url_kept,
                }
            await write_debug_json(
                uni.name,
                {
                    "university": uni.name,
                    "home_url": home,
                    "discovery_mode": discovery_mode,
                    "discovery_by_strategy": crawl.discovery_by_strategy,
                    "candidates_ranked": [
                        {
                            "url": candidate["url"],
                            "score": candidate.get("heuristic_score", 0.0),
                            "source": candidate.get("source_type", ""),
                            "source_strategy": candidate.get("source_strategy", ""),
                            "source_stage": candidate.get("source_stage", ""),
                            "page_family": candidate.get("page_family", "generic"),
                            "parent_url": candidate.get("parent_url", ""),
                            "anchor_text": candidate.get("anchor_text", ""),
                            "selected_for_probe": idx < config.PROBE_LIMIT,
                        }
                        for idx, candidate in enumerate(cands[: config.PROBE_LIMIT])
                    ],
                    "probed_pages": [
                        {
                            "url": page.get("url", ""),
                            "raw_contacts": page.get("raw_contacts", 0),
                            "kept_contacts": len(page.get("kept_contacts", [])),
                            "details": {
                                "url": page.get("url", ""),
                                "raw_contacts": page.get("raw_contacts", 0),
                                "kept_contacts": page.get("kept_contacts", []),
                                "rejected_contacts": page.get("rejected_contacts", []),
                                "missing_email_candidates": page.get("missing_email_candidates", []),
                                "page_length": page.get("page_length", 0),
                                "mailto_count": page.get("mailto_count", 0),
                                "pages_fetched": page.get("pages_fetched", 0),
                                "source_breakdown": page.get("source_breakdown", {}),
                            },
                        }
                        for page in crawl.probe_summary.page_results
                    ],
                    "best_page": best_page,
                    "missing_email_candidates": crawl.probe_summary.missing_email_candidates,
                    "rejected_contacts": crawl.probe_summary.rejected_contacts,
                    "deduped_contacts": crawl.probe_summary.deduped_contacts,
                },
            )

        if per_uni_max and per_uni_max > 0 and uni_pool:
            topk_sorted = sorted(uni_pool, key=lambda item: item[0], reverse=True)
            results.extend(row for (_, _, row) in topk_sorted)


async def process_uni_with_retry(
    uni: Target,
    country: str,
    sem: asyncio.Semaphore,
    results: List[Dict[str, str]],
    seen: Set[Tuple[str, str]],
    stats: Dict[str, int],
    emit_all: bool,
    per_uni_max: Optional[int],
    verify_names: bool,
    discovery_mode: str,
):
    """Retry a university with exponential backoff on failure."""
    delay = config.UNI_BACKOFF_BASE
    last_err: Optional[Exception] = None
    for attempt in range(1, config.UNI_MAX_RETRIES + 1):
        try:
            await process_uni(
                uni,
                country,
                sem,
                results,
                seen,
                stats,
                emit_all,
                per_uni_max,
                verify_names,
                discovery_mode,
            )
            return
        except Exception as exc:
            last_err = exc
            LOG.warning(
                "process_uni failed (attempt %d/%d) for %s: %s",
                attempt,
                config.UNI_MAX_RETRIES,
                uni.name,
                exc,
            )
            if attempt == config.UNI_MAX_RETRIES:
                break
            await asyncio.sleep(delay + random.uniform(0, 0.5))
            delay = min(delay * 2, config.UNI_BACKOFF_MAX)
    if last_err:
        raise last_err


async def main(
    country: str,
    limit: Optional[int],
    outfile: str,
    emit_all: bool,
    debug: bool,
    debug_dir: Optional[str],
    ignore_robots: bool,
    verbose: bool,
    browser_ua: bool,
    per_uni_max: Optional[int],
    verify_names: bool,
    discovery_mode: str = "heuristic_only",
):
    """Main crawl orchestration."""
    if verbose:
        logging.getLogger().setLevel(logging.DEBUG)
        LOG.setLevel(logging.DEBUG)

    async with pipeline_runtime(
        ignore_robots=ignore_robots,
        debug=debug,
        debug_dir=debug_dir,
        browser_ua=browser_ua,
        training_csv=True,
    ):
        sem = asyncio.Semaphore(config.CONCURRENCY)
        results: List[Dict[str, str]] = []
        seen: Set[Tuple[str, str]] = set()
        stats = {"tok_in": 0, "tok_out": 0}

        targets = await collect_targets(OpenAlexSource(), country, limit)
        if not targets:
            print("No universities found from OpenAlex; check country code.")
            return

        tasks = [
            process_uni_with_retry(
                target,
                country,
                sem,
                results,
                seen,
                stats,
                emit_all,
                per_uni_max,
                verify_names,
                discovery_mode,
            )
            for target in targets
        ]

        for task in tqdm(asyncio.as_completed(tasks), total=len(tasks), unit="uni", desc="universities"):
            try:
                await task
            except Exception as exc:
                LOG.warning("[warn] task error: %s", repr(exc))

        if not results:
            print("No contacts found")
            return

        df = pd.DataFrame(results).sort_values(["University", "Score"], ascending=[True, False])
        from pathlib import Path as PathlibPath

        if PathlibPath(outfile).suffix.lower() in {".xlsx", ".xls"}:
            try:
                df.to_excel(outfile, index=False)
            except ModuleNotFoundError:
                outfile = str(PathlibPath(outfile).with_suffix(".csv"))
                df.to_csv(outfile, index=False, quoting=csv.QUOTE_MINIMAL)
        else:
            df.to_csv(outfile, index=False, quoting=csv.QUOTE_MINIMAL)

        dollars = cost_for_tokens(stats["tok_in"], stats["tok_out"])
        print(f"[ok] {len(df)} rows -> {outfile}\n~{stats['tok_in']}/{stats['tok_out']} tokens  ~  ${dollars:.4f}")
        if config.DEBUG_ENABLED:
            print(f"Debug JSON files + training CSV at: {config.DEBUG_DIR.resolve()}")


async def run_all(
    country: str,
    limit: Optional[int],
    outfile: str,
    emit_all: bool,
    debug: bool,
    debug_dir: Optional[str],
    ignore_robots: bool,
    verbose: bool,
    browser_ua: bool,
    per_uni_max: Optional[int],
    verify_names: bool,
    discovery_mode: str = "heuristic_only",
):
    """Entry point for external callers."""
    await main(
        country,
        limit,
        outfile,
        emit_all,
        debug,
        debug_dir,
        ignore_robots,
        verbose,
        browser_ua,
        per_uni_max,
        verify_names,
        discovery_mode,
    )
