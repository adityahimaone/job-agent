{# How to edit: Bahasa Indonesia. Tiga paragraf pendek. Fakta hanya dari candidate_profile.md. #}
{{ greeting }}

Saya {{ candidate.name }}, {{ candidate.headline }} yang berbasis di {{ candidate.location }} ({{ candidate.timezone }}). Saya melamar posisi {{ job.title }} di {{ job.company }}. Dengan {{ candidate.years_experience }}+ tahun membangun aplikasi web production, saya berfokus pada {{ requirements.must_have|join(', ') }}{% if extra_skills %} dan punya pengalaman langsung dengan {{ extra_skills|join(', ') }}{% endif %}.

Beberapa highlight yang sesuai dengan kebutuhan:
{% for e in evidence %}
- {{ e }}
{% endfor %}
Saya mencari pekerjaan {{ candidate.constraints.availability }} dan nyaman berkolaborasi lintas {{ candidate.constraints.timezone_alignment }}. Saya ingin mendiskusikan bagaimana saya bisa membantu {{ job.company }} mengirimkan antarmuka yang andal dan rapi. Portofolio saya: {{ candidate.links.portfolio }}.

{{ signoff }}