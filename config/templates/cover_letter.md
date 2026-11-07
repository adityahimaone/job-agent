{# How to edit: Three short paragraphs. Only use grounded facts from candidate_profile.md. Variables: candidate.*, job.*, requirements.*, evidence (list), matched_skills (list), greeting, signoff. #}
{{ greeting }}

I'm {{ candidate.name }}, a {{ candidate.headline }} based in {{ candidate.location }} ({{ candidate.timezone }}), applying for the {{ job.title }} role at {{ job.company }}. With {{ candidate.years_experience }}+ years of experience building production web applications—leading frontend architecture for a 15K+ user job-seeker platform and shipping features across a multi-product B2B SaaS HRIS ecosystem—I focus heavily on {{ requirements.must_have|join(', ') }}{% if extra_skills %} and bring hands-on experience with {{ extra_skills|join(', ') }}{% endif %}.

A few highlights from my production experience that line up with what you're looking for:
{% for e in evidence %}
- {{ e }}
{% endfor %}
I'm open to {{ candidate.constraints.availability }} opportunities and collaborate comfortably across {{ candidate.constraints.timezone_alignment }}. I'd love to discuss how I can help {{ job.company }} ship reliable, scalable, and well-crafted interfaces. You can explore my work at {{ candidate.links.portfolio }} and my code at {{ candidate.links.github }}.

{{ signoff }}