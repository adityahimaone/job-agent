"""Job discovery through ego(lite).

Two facts shaped this module, both found by probe rather than assumption:

1. ego-browser's `console.log` arrives on stderr, so the client reads both streams.
2. LinkedIn's job page is a virtualised React app: the cards are never in the DOM,
   so scraping `li.job-card-container` finds nothing. The guest JSON-HTML endpoint
   `seeMoreJobPostings/search` returns the same cards as plain HTML and works
   signed in or out. Its markup is parsed with regex, not DOMParser: the page
   enforces Trusted Types, and assigning that HTML into a div yields nothing.

ponytail: LinkedIn only. Wellfound needs a signed-in session and its markup is
not verified yet, so it raises instead of returning a silent empty list.
"""

from __future__ import annotations

import hashlib
import json
from urllib.parse import urlencode

from job_agent.db.models import Platform
from job_agent.platforms.base import JobPosting
from job_agent.platforms.ego.client import EgoClient, EgoError
from job_agent.utils.logging import get_logger

log = get_logger(__name__)

LINKEDIN_GUEST_API = (
    "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
)
LINKEDIN_ORIGIN = "https://www.linkedin.com/jobs/"
PAGE_SIZE = 25

EXPERIENCE_LEVEL_MAP = {
    "internship": "1",
    "entry": "2",
    "junior": "2",
    "associate": "3",
    "mid": "3",
    "senior": "4",
    "lead": "5",
    "director": "6",
}

SEARCH_SCRIPT = r"""
const cfg = __CFG__;
const task = await taskSpace(cfg.space);
const page = task.page("p1");
await page.goto(cfg.origin);
const dec = (s) =>
  s
    .replace(/<[^>]*>/g, " ")
    .replace(/&amp;/g, "&")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&#39;/g, "'")
    .replace(/&quot;/g, '"')
    .replace(/&nbsp;/g, " ")
    .replace(/\s+/g, " ")
    .trim();
const cut = (chunk, re) => {
  const m = chunk.match(re);
  return m ? dec(m[1]) : "";
};
const rows = [];
let status = 0;
let pages = 0;
for (const start of cfg.starts) {
  if (rows.length >= cfg.limit) break;
  const qs = new URLSearchParams({ keywords: cfg.query, start: String(start) });
  if (cfg.location) qs.set("location", cfg.location);
  if (cfg.remote) qs.set("f_WT", "2");
  if (cfg.exp) qs.set("f_E", cfg.exp);
  const res = await page.evaluate(
    async (u) => {
      const r = await fetch(u, {
        headers: { accept: "text/html" },
        credentials: "include",
      });
      return { status: r.status, html: await r.text() };
    },
    cfg.api + "?" + qs.toString()
  );
  status = res.status;
  pages += 1;
  const chunks = res.html.split("<li").slice(1);
  let added = 0;
  for (const c of chunks) {
    const title = cut(
      c,
      /class="[^"]*base-search-card__title[^"]*"[^>]*>([\s\S]*?)<\/h3>/
    );
    if (!title) continue;
    const href = cut(c, /href="(https:\/\/[^"]*\/jobs\/view\/[^"]*)"/);
    const id =
      cut(c, /data-entity-urn="urn:li:jobPosting:(\d+)"/) ||
      (href.match(/view\/(\d+)/) || [])[1] ||
      "";
    rows.push({
      id,
      url: (href || "").split("?")[0],
      title,
      company: cut(
        c,
        /class="[^"]*base-search-card__subtitle[^"]*"[^>]*>([\s\S]*?)<\/h4>/
      ),
      location: cut(
        c,
        /class="[^"]*job-search-card__location[^"]*"[^>]*>([\s\S]*?)<\/span>/
      ),
      posted: cut(c, /<time[^>]*datetime="([^"]+)"/),
    });
    added += 1;
    if (rows.length >= cfg.limit) break;
  }
  if (!added) break;
}
console.log("__RESULT__" + JSON.stringify({ status, pages, rows }));
"""

DETAIL_SCRIPT = r"""
const cfg = __CFG__;
const task = await taskSpace(cfg.space);
const page = task.page("p1");
await page.goto(cfg.url);
await new Promise((r) => setTimeout(r, cfg.settle));
const body = await page.evaluate(() => {
  const pick = (sels) => {
    for (const s of sels) {
      const e = document.querySelector(s);
      if (e && e.innerText && e.innerText.trim().length > 80) {
        return e.innerText.trim();
      }
    }
    return "";
  };
  return (
    pick([
      ".jobs-description__content",
      ".jobs-box__html-content",
      "#job-details",
      ".job-description",
      "article",
      "main",
    ]) || document.body.innerText
  ).slice(0, 20000);
});
console.log("__RESULT__" + JSON.stringify({ title: await page.title(), body }));
"""


def _external_id(row: dict) -> str:
    if row.get("id"):
        return str(row["id"])
    return hashlib.sha1(row["url"].encode()).hexdigest()[:16]


class EgoDiscovery:
    """Search and read job pages inside ego(lite)."""

    def __init__(
        self,
        client: EgoClient,
        platform: str = "linkedin",
        *,
        task_space: str = "job-agent",
        settle_ms: int = 2500,
    ):
        if platform != "linkedin":
            raise ValueError(
                f"ego discovery supports linkedin only, not {platform!r} (ponytail)"
            )
        self.client = client
        self.platform = platform
        self.task_space = task_space
        self.settle_ms = settle_ms

    def search(
        self,
        query: str,
        location: str = "",
        remote: bool = False,
        experience_level: str = "",
        limit: int = 25,
    ) -> list[JobPosting]:
        cfg = {
            "space": self.task_space,
            "origin": LINKEDIN_ORIGIN,
            "api": LINKEDIN_GUEST_API,
            "query": query,
            "location": location,
            "remote": bool(remote),
            "exp": EXPERIENCE_LEVEL_MAP.get(experience_level.lower(), "")
            if experience_level
            else "",
            "limit": limit,
            "starts": list(range(0, max(limit, PAGE_SIZE) + PAGE_SIZE, PAGE_SIZE)),
        }
        try:
            result = self.client.run(SEARCH_SCRIPT.replace("__CFG__", json.dumps(cfg)))
        except EgoError as exc:
            log.warning("ego_search_failed", platform=self.platform, error=str(exc)[:300])
            return []

        rows = result.get("rows") or []
        log.info(
            "ego_search_done",
            platform=self.platform,
            rows=len(rows),
            pages=result.get("pages", 0),
            status=result.get("status", 0),
        )
        if not rows and result.get("status") not in (200, 0):
            log.warning("ego_search_no_rows", status=result.get("status"))

        postings: list[JobPosting] = []
        for row in rows[:limit]:
            if not row.get("title") or not row.get("url"):
                continue
            postings.append(
                JobPosting(
                    external_id=_external_id(row),
                    platform=Platform(self.platform),
                    title=row["title"],
                    company=row.get("company", ""),
                    location=row.get("location", ""),
                    url=row["url"],
                    remote=remote
                    or "remote" in (row.get("location") or "").lower(),
                    extra={"posted": row.get("posted", "")},
                )
            )
        return postings

    def details(self, job_url: str) -> str:
        cfg = {
            "space": self.task_space,
            "url": job_url,
            "settle": self.settle_ms,
        }
        try:
            return self.client.run(DETAIL_SCRIPT.replace("__CFG__", json.dumps(cfg))).get(
                "body", ""
            )
        except EgoError as exc:
            log.warning("ego_detail_failed", url=job_url, error=str(exc)[:300])
            return ""