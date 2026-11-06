{# How to edit: The FIRST line must stay "Subject: ...". Everything after the first blank line is the email body. Only use grounded facts from candidate_profile.md. #}
Subject: {{ job.title }} at {{ job.company }} — {{ candidate.name }}

{{ greeting }}

I came across the {{ job.title }} role at {{ job.company }} and wanted to reach out directly. I'm a {{ candidate.headline }} with {{ candidate.years_experience }}+ years of experience building production web applications.

A few relevant highlights:
{% for e in evidence %}
- {{ e }}
{% endfor %}
Skills that line up with your requirements: {{ matched_skills|join(', ') }}. I'm available for {{ candidate.constraints.availability }} work and align with {{ candidate.constraints.timezone_alignment }}.

You can see my work at {{ candidate.links.portfolio }}. Thanks for your time.

{{ signoff }}
