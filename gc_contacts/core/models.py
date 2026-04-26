"""
Data models and type definitions.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class Target:
    """
    A unified target entity to crawl.
    Replaces implicit university-only assumptions.
    All sources must return Target instances.
    """
    name: str
    url: str
    country: Optional[str] = None
    org_type: str = "university"   # "university" | "company" | "ngo" | etc.
    source: Optional[str] = None   # "openalex" | "commercial" | "csv" | etc.
    metadata: Optional[dict] = None


@dataclass
class Contact:
    """Extracted contact information."""
    role: str = ""
    name: str = ""
    email: str = ""
    page_url: str = ""

    def to_dict(self) -> Dict[str, str]:
        return {
            "role": self.role,
            "name": self.name,
            "email": self.email,
            "page_url": self.page_url,
        }


@dataclass
class Candidate:
    """A candidate page that might contain contacts."""
    url: str
    source_type: str  # llm, nav, sitemap, heuristic, subdomain, wp, drupal
    anchor_text: str = ""
    heuristic_score: float = 0.0


@dataclass
class University:
    """University/institution to process (legacy; prefer Target)."""
    name: str
    url: str


@dataclass
class ProcessResult:
    """Result of processing a single university."""
    university_name: str
    country: str
    contacts: List[Dict[str, str]] = field(default_factory=list)
    debug_json: Optional[Dict] = None
    stats: Dict[str, int] = field(default_factory=lambda: {"raw_count": 0, "kept_count": 0})


@dataclass
class URLFeatures:
    """Extracted features from a URL for classification."""
    depth: int
    path_tokens: int
    subdomain: str
    ext: str
