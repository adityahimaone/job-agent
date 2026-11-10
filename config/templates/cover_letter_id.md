{# How to edit: Bahasa Indonesia. Tiga paragraf pendek. Fakta hanya dari candidate_profile.md. #}
{{ greeting }}

Saya {{ candidate.name }}, {{ candidate.headline }} yang berbasis di {{ candidate.location }} ({{ candidate.timezone }}), bermaksud melamar posisi {{ job.title.split('|')[0].strip() }} di {{ job.company }}. Dengan pengalaman {{ candidate.years_experience }}+ tahun membangun aplikasi web production, saya berfokus pada {{ requirements.must_have|join(', ') }}{% if extra_skills %} serta memiliki pengalaman langsung dengan {{ extra_skills|join(', ') }}{% endif %}.

Beberapa pencapaian relevan yang selaras dengan kebutuhan tim Anda meliputi:
{% for e in evidence %}
- {{ e }}
{% endfor %}
Saya terbuka untuk peluang kerja {{ candidate.constraints.availability_id or candidate.constraints.availability }} dan nyaman berkolaborasi lintas zona waktu ({{ candidate.constraints.timezone_alignment_id or candidate.constraints.timezone_alignment }}). Saya sangat antusias untuk mendiskusikan bagaimana pengalaman saya dalam merancang arsitektur frontend yang scalable, design systems, dan antarmuka yang rapi dapat membantu {{ job.company }}. Portofolio saya dapat diakses di {{ candidate.links.portfolio }} serta GitHub di {{ candidate.links.github }}.

{{ signoff }}