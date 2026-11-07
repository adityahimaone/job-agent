{# How to edit: The FIRST line must stay "Subject: ...". Everything after the first blank line is the email body. Only use grounded facts from candidate_profile.md. #}
Subject: {{ job.title }} at {{ job.company }} — {{ candidate.name }}

{{ greeting }}

I'm reaching out directly regarding the {{ job.title }} role at {{ job.company }}. I'm a {{ candidate.headline }} with {{ candidate.years_experience }}+ years of experience building production web applications—ranging from leading frontend development on a 15K+ user job-seeker platform to shipping core features across a multi-product B2B SaaS HRIS ecosystem.

A few relevant highlights from my work:
{% for e in evidence %}
- {{ e }}
{% endfor %}
My core stack lines up directly with your requirements: {{ matched_skills|join(', ') }}. I'm available for {{ candidate.constraints.availability }} and align comfortably with {{ candidate.constraints.timezone_alignment }}.

I've attached my resume for full context, and you can review my live work and repositories here:
- Portfolio: {{ candidate.links.portfolio }}
- GitHub: {{ candidate.links.github }}
- LinkedIn: {{ candidate.links.linkedin }}

Thanks for your time and consideration. I'd welcome a quick chat to discuss how I can contribute to the engineering team at {{ job.company }}.

{{ signoff }}