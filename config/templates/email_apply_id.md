{# How to edit: Bahasa Indonesia. Baris pertama HARUS tetap "Subject: ...". Fakta hanya dari candidate_profile.md. #}
Subject: Lamaran: {{ job.title }} di {{ job.company }} — {{ candidate.name }}

{{ greeting }}

Melalui email ini, saya bermaksud mengajukan lamaran untuk posisi {{ job.title.split('|')[0].strip() }} di {{ job.company }}. Saya adalah {{ candidate.headline }} dengan pengalaman {{ candidate.years_experience }}+ tahun dalam membangun aplikasi web skala produksi yang responsif, scalable, dan mudah dipelihara (maintainable)—khususnya menggunakan {{ matched_skills|join(', ') }}.

Beberapa rekam jejak dan keahlian teknis saya yang selaras dengan kebutuhan di {{ job.company }}:
{% for e in evidence %}
- {{ e }}
{% endfor %}

Saat ini saya terbuka untuk skema kerja {{ candidate.constraints.availability_id or candidate.constraints.availability }} dan terbiasa bekerja dalam tim lintas fungsi ({{ candidate.constraints.timezone_alignment_id or candidate.constraints.timezone_alignment }}).

Sebagai bahan pertimbangan dan tinjauan teknis, Bapak/Ibu dapat mengeksplorasi hasil karya, dokumentasi sistem desain, serta repositori kode yang telah saya bangun melalui tautan berikut:
- Website & Showcase Utama: {{ candidate.links.portfolio }}
- Portofolio Proyek Pilihan: {{ candidate.links.projects }}
- Dokumentasi Design System & UI: {{ candidate.links.design_system }}
- Repositori GitHub: {{ candidate.links.github }}

Bersama email ini turut saya lampirkan CV terbaru saya. Terima kasih atas waktu dan perhatian Bapak/Ibu. Saya sangat antusias apabila diberikan kesempatan untuk berdiskusi lebih lanjut dan mendemonstrasikan proyek-proyek tersebut secara langsung.

{{ signoff }}
WhatsApp: {{ candidate.phone }}
LinkedIn: {{ candidate.links.linkedin }}