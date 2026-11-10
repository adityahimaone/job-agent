{# How to edit: Bio singkat bahasa Indonesia untuk expert network. #}
# {{ candidate.name }} — {{ candidate.headline }}

{{ candidate.headline }} dengan pengalaman {{ candidate.years_experience }}+ tahun membangun aplikasi web production. Saya bekerja lintas coding, design system, dan workflow automation AI/LLM.{% if matched_skills %} Stack utama: {{ matched_skills|join(', ') }}.{% endif %}

Pekerjaan terakhir yang relevan:
{% for e in evidence %}
- {{ e }}
{% endfor %}

Saya baru saja mengirim SaaS products, sistem HR internal, dan antarmuka berbasis design system, dan saya senang mengevaluasi serta memperbaiki alur kerja coding berbantuan AI. Saat ini saya sedang menjajaki kesempatan {{ job.title }} di {{ job.company }} dan jaringan expert sejenis, serta terbuka untuk pekerjaan {{ candidate.constraints.availability }} ({{ candidate.constraints.timezone_alignment }}).

Portofolio: {{ candidate.links.portfolio }} · {{ candidate.email }}