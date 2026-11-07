{# How to edit: Bahasa Indonesia. Baris pertama HARUS tetap "Subject: ...". Fakta hanya dari candidate_profile.md. #}
Subject: {{ job.title }} di {{ job.company }} — {{ candidate.name }}

{{ greeting }}

Saya menemukan lowongan {{ job.title }} di {{ job.company }} dan ingin menghubungi langsung. Saya adalah {{ candidate.headline }} dengan pengalaman {{ candidate.years_experience }}+ tahun membangun aplikasi web production.

Beberapa highlight yang relevan:
{% for e in evidence %}
- {{ e }}
{% endfor %}
Keahlian yang sesuai dengan kebutuhan: {{ matched_skills|join(', ') }}. Saya tersedia untuk pekerjaan {{ candidate.constraints.availability }} dan selaras dengan {{ candidate.constraints.timezone_alignment }}.

Portofolio saya: {{ candidate.links.portfolio }}. Terima kasih atas waktu bapak/ibu.

{{ signoff }}