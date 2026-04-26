"""
Base crawl profile interface.

A CrawlProfile controls:
  - which extra URL slugs to probe
  - which discovery mode to prefer
  - which role keywords are positive / negative signals
  - the minimum contact score to keep a contact
  - whether generic inboxes are allowed
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import List


@dataclass
class CrawlProfile:
    """
    Configuration bundle passed to the crawl engine for one pipeline run.

    Attributes:
        name:                  Human-readable profile name.
        discovery_mode:        Discovery strategy mode (hybrid, heuristic_only, etc.).
        slug_hints:            Extra URL paths to probe (injected into discover.gather_candidates).
        role_positive_keywords: Role text that increases contact score.
        role_negative_keywords: Role text that decreases contact score.
        min_contact_score:     Minimum score for keep_contact() to accept a contact.
        allow_generic_emails:  If True, generic inboxes (info@, international@) are kept.
    """
    name: str
    discovery_mode: str = "hybrid"
    slug_hints: List[str] = field(default_factory=list)
    role_positive_keywords: List[str] = field(default_factory=list)
    role_negative_keywords: List[str] = field(default_factory=list)
    min_contact_score: int = 6
    allow_generic_emails: bool = False
