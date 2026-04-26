"""
NAFSA outreach crawl profile.

Targets organisations that are likely to have international partnership,
global engagement, and study abroad offices. Produces CRM-ready contact
datasets rather than benchmark outputs.

Key differences from academic profile:
  - Broader slug hints (partnerships, exchange, mobility)
  - Different positive role keywords (partnerships, engagement, mobility)
  - Suppression of pure student-recruitment contacts
  - Generic programme inboxes are ALLOWED (partnerships@, international@)
  - Lower min_contact_score (more permissive filtering for outreach)
"""

from gc_contacts.profiles.base_profile import CrawlProfile

NAFSA_PROFILE = CrawlProfile(
    name="nafsa",
    discovery_mode="hybrid",

    # URL paths indicating international partnership / engagement teams
    slug_hints=[
        "/international-office",
        "/international-relations",
        "/international-relations-office",
        "/global-engagement",
        "/office-of-global-engagement",
        "/international-partnerships",
        "/global-partnerships",
        "/global-partnerships-team",
        "/mobility",
        "/exchange",
        "/study-abroad",
        "/studyabroad",
        "/collaboration",
        "/global-strategy",
        "/global-opportunities",
        "/internationalisation",
        "/internationalization",
        "/international-cooperation",
        "/erasmus",
        "/erasmus-plus",
        "/international/partnerships",
        "/international/team",
        "/partnerships",
        "/global",
        "/contact/international",
        "/about/international",
        "/about/global",
        "/offices/international",
        "/offices/global-engagement",
        "/offices/international-relations",
    ],

    # Roles strongly relevant to partnership outreach
    role_positive_keywords=[
        "international partnerships",
        "global engagement",
        "international relations",
        "exchange programs",
        "exchange programme",
        "mobility programs",
        "mobility programme",
        "partnerships",
        "business development",
        "global strategy",
        "institutional advancement",
        "study abroad",
        "global opportunities",
        "internationalisation",
        "internationalization",
        "international cooperation",
        "strategic partnerships",
        "strategic partnership",
        "office of global engagement",
        "erasmus",
        "erasmus coordinator",
        "collaboration",
        "external relations",
        "international cooperation",
    ],

    # Roles that are NOT targets for NAFSA outreach
    role_negative_keywords=[
        "student recruitment",
        "undergraduate admissions",
        "postgraduate admissions",
        "student services",
        "student support",
        "domestic admissions",
    ],

    # More permissive — outreach datasets include programme-level contacts
    min_contact_score=4,

    # Allow generic inboxes relevant to partnerships
    allow_generic_emails=True,
)
