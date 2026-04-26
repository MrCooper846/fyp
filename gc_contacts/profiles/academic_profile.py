"""
Academic / benchmark crawl profile.

Replicates the current benchmark behaviour exactly so that
academic_pipeline.py produces identical outputs to the original
benchmark_runner.py.
"""

from gc_contacts.profiles.base_profile import CrawlProfile

ACADEMIC_PROFILE = CrawlProfile(
    name="academic",
    discovery_mode="heuristic_only",

    # Extra slug hints on top of config.SLUGS (academic offices)
    slug_hints=[
        "/admissions",
        "/apply",
        "/recruitment",
        "/international-office",
        "/international-students",
        "/international/contact",
        "/international/team",
        "/about/leadership",
        "/about/management",
        "/about/executive",
        "/leadership",
        "/governance",
        "/directory",
        "/people",
        "/staff",
    ],

    # Role words that are strong positive signals for academic contacts
    role_positive_keywords=[
        "admissions",
        "recruitment",
        "international office",
        "international relations",
        "global engagement",
        "vice chancellor",
        "vice-chancellor",
        "president",
        "rector",
        "provost",
        "chancellor",
    ],

    # No negative keywords for academic mode (keep broad)
    role_negative_keywords=[],

    # Minimum score — mirrors the original keep_contact() threshold logic
    # (actual .edu vs rest logic is embedded in keep_contact; this acts
    #  as a lower bound unless overridden)
    min_contact_score=5,

    # Personal named contacts only; no generic inboxes
    allow_generic_emails=False,
)
