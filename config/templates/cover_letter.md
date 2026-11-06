{# How to edit: Three short paragraphs. Only use grounded facts from candidate_profile.md. Variables: candidate.*, job.*, requirements.*, evidence (list), matched_skills (list), greeting, signoff. #}
{{ greeting }}

I'm {{ candidate.name }}, a {{ candidate.headline }} based in {{ candidate.location }} ({{ candidate.timezone }}). I'm applying for the {{ job.title }} role at {{ job.company }}. With {{ candidate.years_experience }}+ years building production web applications, I focus on {{ requirements.must_have|join(', ') }} and bring hands-on experience with {{ matched_skills|join(', ') }}.

A few highlights that line up with what you're looking for:
{% for e in evidence %}
- {{ e }}
{% endfor %}
I'm looking for {{ candidate.constraints.availability }} work and I'm comfortable collaborating across {{ candidate.constraints.timezone_alignment }}. I'd love to talk about how I can help {{ job.company }} ship reliable, well-crafted interfaces. My work is at {{ candidate.links.portfolio }}.

{{ signoff }}
