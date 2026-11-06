{# How to edit: Short bio for AI-training / expert networks. Emphasise coding, design systems, and AI workflow automation. Only use grounded facts from candidate_profile.md. #}
# {{ candidate.name }} — {{ candidate.headline }}

{{ candidate.headline }} with {{ candidate.years_experience }}+ years building production web applications. I work across coding, design systems, and AI/LLM workflow automation: {{ matched_skills|join(', ') }}.

Recent, relevant work:
{{ evidence|join('\n') }}

I've shipped SaaS products, internal HR systems, and design-system-driven interfaces, and I enjoy evaluating and improving AI-assisted coding workflows. I'm currently exploring {{ job.title }} opportunities at {{ job.company }} and similar expert networks, and I'm open to {{ candidate.constraints.availability }} engagements ({{ candidate.constraints.timezone_alignment }}).

Portfolio: {{ candidate.links.portfolio }} · {{ candidate.email }}
