"""
gc_contacts: Modular contact discovery crawler for universities/institutions.

Public API
----------
Pipelines:
    from gc_contacts.pipelines.academic_pipeline import AcademicPipeline
    from gc_contacts.pipelines.nafsa_pipeline    import NafsaPipeline

Sources:
    from gc_contacts.sources.openalex_source import OpenAlexSource
    from gc_contacts.sources.company_source  import CompanySource

Profiles:
    from gc_contacts.profiles.academic_profile import ACADEMIC_PROFILE
    from gc_contacts.profiles.nafsa_profile    import NAFSA_PROFILE

Exporters:
    from gc_contacts.exporters.benchmark_exporter import BenchmarkExporter
    from gc_contacts.exporters.crm_exporter       import CRMExporter

Agent:
    from gc_contacts.agent.contact_classifier import classify_contact

Core data models:
    from gc_contacts.core.models import Contact, Candidate, University,
                                        ProcessResult, URLFeatures, Target
"""

__version__ = "4.0"

# ── Convenience re-exports ────────────────────────────────────────────────────
from gc_contacts.core.models import (  # noqa: F401
    Contact,
    Candidate,
    University,
    ProcessResult,
    URLFeatures,
    Target,
)
