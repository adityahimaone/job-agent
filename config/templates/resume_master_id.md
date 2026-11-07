{# How to edit: Resume bahasa Indonesia. Semua fakta tetap dari candidate_profile.md. #}
# {{ candidate.name }}

{{ candidate.headline }}
{{ candidate.location }} · {{ candidate.timezone }} · {{ candidate.email }}
{{ candidate.links.portfolio }} · {{ candidate.links.linkedin }} · {{ candidate.links.github }}

<!-- Dibuat untuk {{ job.title }} di {{ job.company }} -->

## Ringkasan

{{ candidate.headline }} dengan pengalaman {{ candidate.years_experience }}+ tahun membangun aplikasi web production — produk SaaS SSR, design system, dan tooling internal. Kuat di {{ candidate.skills_flat|join(', ') }}. Mencari peran {{ candidate.constraints.availability }}.

## Pengalaman
{% for role in candidate.roles %}
### {{ role.title }} | {{ role.company }} | {{ role.location }} | {{ role.dates }}
{% for b in role.bullets %}
- {{ b }}
{% endfor %}
{% endfor %}

## Proyek
{% for p in candidate.projects %}
### {{ p.name }}{% if p.stack %} | {{ p.stack }}{% endif %}{% if p.url %} | {{ p.url }}{% endif %}
{% if p.blurb %}
{{ p.blurb }}
{% endif %}
{% endfor %}

## Keahlian
{% for cat, items in candidate.skills.items() %}
- **{{ cat }}:** {{ items|join(', ') }}
{% endfor %}

## Pendidikan
{% for e in candidate.education %}
- {{ e }}
{% endfor %}