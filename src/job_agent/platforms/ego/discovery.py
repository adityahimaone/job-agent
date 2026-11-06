"""Job discovery through ego(lite).

Two facts shaped this module, both found by probe rather than assumption:

1. ego-browser's `console.log` arrives on stderr, so the client reads both streams.
2. LinkedIn's job page is a virtualised React app: the cards are never in the DOM,
   so scraping `li.job-card-container` finds nothing. The guest JSON-HTML endpoint
   `seeMoreJobPostings/search` returns the same cards as plain HTML and works
   signed in or out. Its markup is parsed with regex, not DOMParser: the page
   enforces Trusted Types, and assigning that HTML into a div yields nothing.

`platform="linkedin"` keeps that fast guest-endpoint path. Every other supported
board (wellfound, jobstreet, glints, kalibrr, seek, or anything in the board
config) uses the generic snapshot / `page.evaluate(document.querySelectorAll)`
path from ``boards.py``. Both reuse ONE named task space for the whole run.
"""

from __future__ import annotations

import hashlib
import json
from urllib.parse import urlencode  # noqa: F401 - kept for API compatibility

from job_agent.db.models import Platform
from job_agent.platforms.base import JobPosting
from job_agent.platforms.ego.boards import (
    BoardSpec,
    build_board_batch_script,
    build_board_script,
    default_board_specs,
    load_board_specs,
    parse_board_rows,
)
from job_agent.platforms.ego.client import EgoClient, EgoError
from job_agent.platforms.ego.session import EgoSession
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

# Batched variant: loops several (query, location) combos in ONE nodejs process.
# Parsing logic mirrors SEARCH_SCRIPT (kept as a pristine constant above).
SEARCH_ALL_SCRIPT = r"""
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
for (const c of cfg.combos) {
  for (const start of cfg.starts) {
    if (rows.length >= cfg.limit) break;
    const qs = new URLSearchParams({ keywords: c.query, start: String(start) });
    if (c.location) qs.set("location", c.location);
    if (c.remote) qs.set("f_WT", "2");
    if (c.exp) qs.set("f_E", c.exp);
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
    const chunks = res.html.split("<li").slice(1);
    let added = 0;
    for (const ch of chunks) {
      const title = cut(
        ch,
        /class="[^"]*base-search-card__title[^"]*"[^>]*>([\s\S]*?)<\/h3>/
      );
      if (!title) continue;
      const href = cut(ch, /href="(https:\/\/[^"]*\/jobs\/view\/[^"]*)"/);
      const id =
        cut(ch, /data-entity-urn="urn:li:jobPosting:(\d+)"/) ||
        (href.match(/view\/(\d+)/) || [])[1] ||
        "";
      rows.push({
        id,
        url: (href || "").split("?")[0],
        title,
        company: cut(
          ch,
          /class="[^"]*base-search-card__subtitle[^"]*"[^>]*>([\s\S]*?)<\/h4>/
        ),
        location: cut(
          ch,
          /class="[^"]*job-search-card__location[^"]*"[^>]*>([\s\S]*?)<\/span>/
        ),
        posted: cut(ch, /<time[^>]*datetime="([^"]+)"/),
      });
      added += 1;
      if (rows.length >= cfg.limit) break;
    }
    if (!added) break;
  }
  if (rows.length >= cfg.limit) break;
}
console.log("__RESULT__" + JSON.stringify({ status, rows }));
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
        board_config_path: str = "",
    ):
        self._specs: dict[str, BoardSpec] = (
            load_board_specs(board_config_path)
            if board_config_path
            else default_board_specs()
        )
        supported = sorted(set(self._specs) | {"linkedin"})
        if platform not in self._specs and platform != "linkedin":
            raise ValueError(
                f"ego discovery does not support platform {platform!r}; "
                f"supported platforms: {', '.join(supported)}"
            )
        self.client = client
        self.platform = platform
        self.task_space = task_space or "job-agent"
        self.settle_ms = settle_ms
        self._session = EgoSession(self.client, self.task_space)
        self._session_used = False
        self._closed = False

    # -- internal helpers ---------------------------------------------------
    def _run(self, script: str) -> dict:
        """Run one script and remember that the task space is in use."""
        self._session_used = True
        return self.client.run(script)

    def _board_spec(self) -> BoardSpec | None:
        return self._specs.get(self.platform)

    @staticmethod
    def _linkedin_cfg(
        query: str,
        location: str,
        remote: bool,
        experience_level: str,
        limit: int,
    ) -> dict:
        return {
            "query": query,
            "location": location,
            "remote": bool(remote),
            "exp": (
                EXPERIENCE_LEVEL_MAP.get(experience_level.lower(), "")
                if experience_level
                else ""
            ),
        }

    @staticmethod
    def _rows_to_linkedin_postings(rows: list[dict], remote: bool) -> list[JobPosting]:
        postings: list[JobPosting] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            if not row.get("title") or not row.get("url"):
                continue
            postings.append(
                JobPosting(
                    external_id=_external_id(row),
                    platform=Platform.LINKEDIN,
                    title=row["title"],
                    company=row.get("company", ""),
                    location=row.get("location", ""),
                    url=row["url"],
                    remote=remote or "remote" in (row.get("location") or "").lower(),
                    extra={"posted": row.get("posted", "")},
                )
            )
        return postings

    # -- public API ---------------------------------------------------------
    def search(
        self,
        query: str,
        location: str = "",
        remote: bool = False,
        experience_level: str = "",
        limit: int = 25,
    ) -> list[JobPosting]:
        if self.platform == "linkedin":
            return self._search_linkedin(
                query, location, remote, experience_level, limit
            )
        return self._search_board(query, location, remote, experience_level, limit)

    def _search_linkedin(
        self,
        query: str,
        location: str,
        remote: bool,
        experience_level: str,
        limit: int,
    ) -> list[JobPosting]:
        cfg = {
            "space": self.task_space,
            "origin": LINKEDIN_ORIGIN,
            "api": LINKEDIN_GUEST_API,
            **self._linkedin_cfg(query, location, remote, experience_level, limit),
            "limit": limit,
            "starts": list(range(0, max(limit, PAGE_SIZE) + PAGE_SIZE, PAGE_SIZE)),
        }
        try:
            result = self._run(SEARCH_SCRIPT.replace("__CFG__", json.dumps(cfg)))
        except EgoError as exc:
            log.warning(
                "ego_search_failed", platform=self.platform, error=str(exc)[:300]
            )
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
        return self._rows_to_linkedin_postings(rows[:limit], remote)

    def _search_board(
        self,
        query: str,
        location: str,
        remote: bool,
        experience_level: str,
        limit: int,
    ) -> list[JobPosting]:
        spec = self._board_spec()
        if spec is None:  # defensive; __init__ already validated
            return []
        script = build_board_script(
            spec,
            query=query,
            location=location,
            limit=limit,
            settle_ms=self.settle_ms,
            space=self.task_space,
        )
        try:
            result = self._run(script)
        except EgoError as exc:
            log.warning(
                "ego_board_search_failed", board=spec.name, error=str(exc)[:300]
            )
            return []
        postings = parse_board_rows(result, spec)
        if remote:
            for p in postings:
                p.remote = True
        log.info("ego_board_search_done", board=spec.name, rows=len(postings))
        return postings[:limit]

    def details(self, job_url: str) -> str:
        cfg = {
            "space": self.task_space,
            "url": job_url,
            "settle": self.settle_ms,
        }
        try:
            return self._run(DETAIL_SCRIPT.replace("__CFG__", json.dumps(cfg))).get(
                "body", ""
            )
        except EgoError as exc:
            log.warning("ego_detail_failed", url=job_url, error=str(exc)[:300])
            return ""

    def get_details(self, job_url: str) -> str:
        """Alias for :meth:`details` (kept for the documented public API)."""
        return self.details(job_url)

    def search_all(
        self,
        queries: list[str],
        locations: list[str],
        limit: int,
    ) -> list[JobPosting]:
        """Run many (query, location) searches in ONE nodejs invocation.

        Results are deduplicated by URL and capped at *limit*. The same named
        task space is reused for every combo.
        """
        combos: list[tuple[str, str]] = []
        seen_combos: set[tuple[str, str]] = set()
        for q in queries or []:
            for loc in locations or [""]:
                key = (q, loc)
                if key not in seen_combos:
                    seen_combos.add(key)
                    combos.append(key)
        if not combos:
            return []

        if self.platform == "linkedin":
            return self._search_all_linkedin(combos, limit)
        return self._search_all_board(combos, limit)

    def _search_all_linkedin(
        self, combos: list[tuple[str, str]], limit: int
    ) -> list[JobPosting]:
        cfg = {
            "space": self.task_space,
            "origin": LINKEDIN_ORIGIN,
            "api": LINKEDIN_GUEST_API,
            "limit": limit,
            "starts": list(range(0, max(limit, PAGE_SIZE) + PAGE_SIZE, PAGE_SIZE)),
            "combos": [
                self._linkedin_cfg(q, loc, False, "", limit) for q, loc in combos
            ],
        }
        try:
            result = self._run(SEARCH_ALL_SCRIPT.replace("__CFG__", json.dumps(cfg)))
        except EgoError as exc:
            log.warning("ego_search_all_failed", error=str(exc)[:300])
            return []
        return self._dedupe(
            self._rows_to_linkedin_postings(result.get("rows") or [], False), limit
        )

    def _search_all_board(
        self, combos: list[tuple[str, str]], limit: int
    ) -> list[JobPosting]:
        spec = self._board_spec()
        if spec is None:
            return []
        script = build_board_batch_script(
            spec,
            combos=combos,
            limit=limit,
            settle_ms=self.settle_ms,
            space=self.task_space,
        )
        try:
            result = self._run(script)
        except EgoError as exc:
            log.warning("ego_board_search_all_failed", error=str(exc)[:300])
            return []
        return self._dedupe(parse_board_rows(result, spec), limit)

    @staticmethod
    def _dedupe(postings: list[JobPosting], limit: int) -> list[JobPosting]:
        seen: set[str] = set()
        out: list[JobPosting] = []
        for p in postings:
            key = (p.url or "").split("#")[0].rstrip("/").lower()
            if not key or key in seen:
                continue
            seen.add(key)
            out.append(p)
            if len(out) >= limit:
                break
        return out

    def close(self) -> None:
        """Finish the task space if it was used. Safe to call twice."""
        if self._closed or not self._session_used:
            return
        self._closed = True
        try:
            self._session.finish()
            log.info("ego_discovery_closed", space=self.task_space)
        except EgoError as exc:
            log.warning("ego_discovery_close_failed", error=str(exc)[:200])
