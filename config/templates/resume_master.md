{# How to edit: This is the master resume in clean Markdown, used as the PDF / Markdown source. Section headings stay generic on purpose. Everything is grounded in candidate_profile.md. Prepared per posting: {{ job.title }} at {{ job.company }}. #}
# {{ candidate.name }}

{{ candidate.headline }}
{{ candidate.location }} · {{ candidate.timezone }} · {{ candidate.email }}
{{ candidate.links.portfolio }} · {{ candidate.links.linkedin }} · {{ candidate.links.github }}

<!-- Tailored context for {{ job.title }} at {{ job.company }} -->

## Summary

{{ candidate.headline }} with {{ candidate.years_experience }}+ years of experience building production web applications — SSR SaaS products, design systems, and internal tooling. Strong in {{ candidate.skills_flat|join(', ') }}. Looking for {{ candidate.constraints.availability }} roles.

## Experience
{% for role in candidate.roles %}
### {{ role.title }} | {{ role.company }} | {{ role.location }} | {{ role.dates }}
{% for b in role.bullets %}
- {{ b }}
{% endfor %}
{% endfor %}
## Projects
{% for p in candidate.projects %}
### {{ p.name }}{% if p.stack %} | {{ p.stack }}{% endif %}{% if p.url %} | {{ p.url }}{% endif %}
{% if p.blurb %}
{{ p.blurb }}
{% endif %}
{% endfor %}
## Skills
{% for cat, items in candidate.skills.items() %}
- **{{ cat }}:** {{ items|join(', ') }}
{% endfor %}
## Education
{% for e in candidate.education %}
- {{ e }}
{% endfor %}
