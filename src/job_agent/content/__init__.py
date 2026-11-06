"""Grounded, human-editable document generation.

Public API (all offline / no AI key required):

    from job_agent.content import (
        CandidateProfile, load_candidate_profile,
        TemplateSet, load_template_set, default_template_text, DEFAULT_TEMPLATES_DIR,
        JobRequirements, extract_requirements,
        check_grounding,
        GeneratedDocuments, generate_documents, save_documents,
    )
"""

from __future__ import annotations

from job_agent.content.candidate import CandidateProfile, load_candidate_profile
from job_agent.content.generator import (
    GeneratedDocuments,
    generate_documents,
    save_documents,
)
from job_agent.content.grounding import check_grounding
from job_agent.content.requirements import JobRequirements, extract_requirements
from job_agent.content.templates import (
    DEFAULT_TEMPLATES_DIR,
    TemplateSet,
    default_template_text,
    load_template_set,
)

__all__ = [
    "CandidateProfile",
    "load_candidate_profile",
    "TemplateSet",
    "load_template_set",
    "default_template_text",
    "DEFAULT_TEMPLATES_DIR",
    "JobRequirements",
    "extract_requirements",
    "check_grounding",
    "GeneratedDocuments",
    "generate_documents",
    "save_documents",
]
