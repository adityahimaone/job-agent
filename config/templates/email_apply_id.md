{# How to edit: Bahasa Indonesia. Baris pertama HARUS tetap "Subject: ...". Fakta hanya dari candidate_profile.md. #}
Subject: Lamaran: {{ job.title }} di {{ job.company }} — {{ candidate.name }}

{{ greeting }}

Saya menghubungi Anda terkait lowongan {{ job.title.split('|')[0].strip() }} di {{ job.company }}. Sebagai {{ candidate.headline }} dengan pengalaman {{ candidate.years_experience }}+ tahun membangun aplikasi web production, latar belakang teknis saya sangat selaras dengan kebutuhan tim Anda—khususnya pada {{ matched_skills|join(', ') }}.

Beberapa rekam jejak relevan dari proyek yang saya tangani:
{% for e in evidence %}
- {{ e }}
{% endfor %}

Dalam keseharian teknis, saya berfokus pada arsitektur frontend yang scalable, design systems yang reusable, serta eksekusi fitur secara end-to-end. Saat ini saya terbuka untuk skema kerja {{ candidate.constraints.availability_id or candidate.constraints.availability }} dan terbiasa berkolaborasi lintas zona waktu ({{ candidate.constraints.timezone_alignment_id or candidate.constraints.timezone_alignment }}).

Sebagai bahan pertimbangan, saya lampirkan CV terbaru saya pada email ini. Bapak/Ibu juga dapat meninjau hasil kerja dan kode saya melalui tautan berikut:
- Portofolio: {{ candidate.links.portfolio }}
- GitHub: {{ candidate.links.github }}
- LinkedIn: {{ candidate.links.linkedin }}

Terima kasih atas waktu dan perhatian Bapak/Ibu. Saya sangat terbuka untuk jadwal diskusi singkat apabila kualifikasi saya sesuai dengan kebutuhan {{ job.company }}.

{{ signoff }}