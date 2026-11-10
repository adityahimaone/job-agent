{# How to edit: The FIRST line must stay "Subject: ...". Everything after the first blank line is the email body. Only use grounded facts from candidate_profile.md. #}
Subject: {{ job.title }} at {{ job.company }} — {{ candidate.name }}

{{ greeting }}

I'm reaching out directly regarding the {{ job.title.split('|')[0].split(' - Remote')[0].strip() }} role at {{ job.company }}. I'm a {{ candidate.headline }} with {{ candidate.years_experience }}+ years of experience building scalable, maintainable production web applications—working extensively with {{ matched_skills|join(', ') }}.

A few relevant highlights from my production experience:
{% for e in evidence %}
- {{ e }}
{% endfor %}

I'm available for {{ candidate.constraints.availability }} and collaborate comfortably across {{ candidate.constraints.timezone_alignment }}.

I've attached my resume for full context, and you can explore my live work, design system documentation, and code here:
- Main Portfolio & Showcase: {{ candidate.links.portfolio }}
- Selected Projects: {{ candidate.links.projects }}
- Design System & UI Components: {{ candidate.links.design_system }}
- GitHub: {{ candidate.links.github }}

Thanks for your time and consideration. I'd welcome a quick chat or live project walkthrough to discuss how I can contribute to the engineering team at {{ job.company }}.

{{ signoff }}
Phone/WhatsApp: {{ candidate.phone }}
LinkedIn: {{ candidate.links.linkedin }}