"""Render a grounded Markdown resume to a one-file PDF (for email attachment).

WeasyPrint only renders HTML -> PDF, so a tiny Markdown subset converter lives
here: headings, bullets, bold, horizontal rules, inline links. No dependency
beyond weasyprint (already installed for dashboard use).
"""

from __future__ import annotations

import re


def _inline(text: str) -> str:
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", r'<a href="\2">\1</a>', text)
    return text


def markdown_to_html(md: str, title: str = "Resume") -> str:
    lines: list[str] = []
    in_list = False
    for raw in (md or "").splitlines():
        line = raw.rstrip()
        if re.match(r"^-{3,}$", line):
            if in_list:
                lines.append("</ul>")
                in_list = False
            lines.append("<hr>")
            continue
        m = re.match(r"^(#{1,3})\s+(.*)$", line)
        if m:
            if in_list:
                lines.append("</ul>")
                in_list = False
            level = len(m.group(1))
            lines.append(f"<h{level}>{_inline(m.group(2))}</h{level}>")
            continue
        if line.startswith("- "):
            if not in_list:
                lines.append("<ul>")
                in_list = True
            lines.append(f"<li>{_inline(line[2:])}</li>")
            continue
        if in_list:
            lines.append("</ul>")
            in_list = False
        if line.strip() == "":
            continue
        if line.startswith("<!--"):
            continue
        lines.append(f"<p>{_inline(line)}</p>")
    if in_list:
        lines.append("</ul>")

    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        f"<title>{title}</title>"
        "<style>"
        "body{font-family:Inter,Helvetica,Arial,sans-serif;color:#111;"
        "margin:48px 52px;font-size:11pt;line-height:1.5;}"
        "h1{font-size:19pt;margin-bottom:0.1em;}h2{font-size:13pt;margin-top:1.2em;"
        "border-bottom:1px solid #999;padding-bottom:2px;}h3{font-size:11.5pt;margin:0.9em 0 0.2em;}"
        "p{margin:0.25em 0;}ul{margin:0.25em 0 0.7em;padding-left:1.2em;}"
        "hr{border:none;border-top:1px solid #ccc;}a{color:#2563eb;text-decoration:none;}"
        "</style></head><body>"
        + "\n".join(lines)
        + "</body></html>"
    )


def render_pdf(markdown: str, title: str = "Resume") -> bytes:
    from weasyprint import HTML

    return HTML(string=markdown_to_html(markdown, title)).write_pdf()
