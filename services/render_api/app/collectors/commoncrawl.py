"""Common Crawl: generic business contact addresses published on South African (.za) websites.

Reads the public crawl index straight from data.commoncrawl.org (the CDX API is unreliable):

1. ``cluster.idx`` is a sorted list of index blocks. The byte range of the ``za,`` section is found
   once per crawl by binary search (range requests) and kept in the cursor.
2. Each index block (about 3000 captures, host-clustered) is fetched by byte range and grouped by
   host. Per host at most ``pages_per_host`` HTML captures are chosen (home page first, then
   contact/about pages).
3. Each chosen capture is fetched as one WARC record by byte range and scanned for email addresses.

Only generic business mailboxes (configured local parts such as info@ or sales@) on the website's
own .za domain are kept. Addresses naming a person are never collected (Common Crawl's terms
exclude harvesting personal information), and nothing is inferred: the record text holds only the
page title and the addresses exactly as published, with the page URL as provenance.

Cursor: ``crawl``, ``za_start``/``za_end`` (byte offsets of the .za section of cluster.idx),
``offset`` (start of the next unprocessed cluster.idx line) and ``host`` (hosts already done in the
current block, so a time-boxed batch resumes mid-block). ``scanned`` counts WARC page fetches.
"""

import copy
import gzip
import json
import re
import time
import zlib
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from urllib.parse import urlsplit

import httpx

from app.collectors.base import Batch, Record, SourceError
from app.collectors.profiles import clean_label
from app.config import rules, settings

DATA = "https://data.commoncrawl.org/"
EMAIL = re.compile(r"(?<![\w.+-])([A-Za-z0-9._%+-]{1,64})@([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,24})(?![\w-])")
TITLE = re.compile(rb"<title[^>]*>(.*?)</title>", re.I | re.S)
TAGS = re.compile(r"<[^>]+>")
ANY_AT = re.compile(r"\S*@\S*")  # anything email-like the strict pattern missed
RETRY_MAX = 300  # throttled hosts queued in the cursor for later batches
RETRY_TRIES = 6  # batches a throttled host is attempted in before it is dropped


def slim(item):
    """The capture fields a page fetch needs; keeps the cursor small."""
    return {k: item[k] for k in ("url", "filename", "offset", "length")}
CONTACT_PATH = re.compile(r"contact|about|kontak|enquir|get-in-touch|reach-us", re.I)


def host_of(url):
    try:
        host = (urlsplit(url).hostname or "").lower().rstrip(".")
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


# South African second-level public suffixes: an address on one of these alone is never a site's own.
ZA_SUFFIXES = {"za", "co.za", "org.za", "ac.za", "gov.za", "net.za", "web.za", "nom.za", "edu.za", "law.za",
               "mil.za", "school.za", "alt.za", "ngo.za", "nis.za", "tm.za", "grondar.za", "agric.za"}


def own_domain(email_domain, host):
    """The address is on the website's own domain (not a web designer's or a directory's)."""
    if email_domain in ZA_SUFFIXES:
        return False
    return email_domain == host or host.endswith("." + email_domain) or email_domain.endswith("." + host)


def business_emails(body, host, cfg):
    """Distinct allowed business mailboxes on the site's own .za domain, in page order."""
    allowed = {x.casefold() for x in cfg.get("business_locals", [])}
    found = []
    for local, domain in EMAIL.findall(body):
        local, domain = local.casefold(), domain.casefold().rstrip(".")
        if local not in allowed or not domain.endswith(".za") or not own_domain(domain, host):
            continue
        address = f"{local}@{domain}"
        if address not in found:
            found.append(address)
    return found[: cfg.get("max_emails_per_host", 3)]


def parse_warc(raw):
    """(timestamp, html bytes) from one gzip WARC response record; (None, b"") if unusable."""
    try:
        data = gzip.decompress(raw)
    except (OSError, EOFError, zlib.error):
        return None, b""
    head, _, rest = data.partition(b"\r\n\r\n")
    stamp = None
    for line in head.split(b"\r\n"):
        if line.lower().startswith(b"warc-date:"):
            try:
                stamp = datetime.fromisoformat(line.split(b":", 1)[1].strip().decode().replace("Z", "+00:00"))
            except ValueError:
                stamp = None
    _, _, body = rest.partition(b"\r\n\r\n")  # skip the HTTP response headers
    return stamp, body


class CommonCrawl:
    def __init__(self, client=None):
        self.client = client

    # ---- HTTP -------------------------------------------------------------------------------
    def _range(self, client, path, start, length, attempts=4, missing=None):
        """Bytes, ``missing`` for a non-retryable answer (404/416), or None after retries."""
        for attempt in range(attempts):
            try:
                response = client.get(DATA + path, headers={"Range": f"bytes={start}-{start + length - 1}"})
            except httpx.HTTPError:
                response = None
            if response is not None and response.status_code in (200, 206):
                return response.content
            if response is not None and response.status_code not in (429, 500, 502, 503, 504):
                return missing  # 404/416: not retryable
            time.sleep(min(8, 0.5 * 2 ** attempt))  # "Slow Down": back off politely
        return None

    def _index(self, crawl):
        return f"cc-index/collections/{crawl}/indexes/"

    def _lines(self, client, crawl, offset):
        """Complete cluster.idx lines in the 4 KB after ``offset``: [(start, line)]. A partial first
        line is skipped unless ``offset`` is 0 (callers keep offsets aligned to line starts)."""
        chunk = self._range(client, self._index(crawl) + "cluster.idx", offset, 4096)
        if chunk is None:
            raise SourceError("commoncrawl_index_unavailable", 300)
        pos, lines = 0, []
        if offset:
            pos = chunk.find(b"\n") + 1
            if pos == 0:
                return []
        while True:
            end = chunk.find(b"\n", pos)
            if end < 0:
                return lines
            lines.append((offset + pos, chunk[pos:end].decode("utf-8", "replace")))
            pos = end + 1

    def _aligned(self, client, crawl, offset):
        """Lines starting exactly at ``offset`` (a known line start)."""
        lines = self._lines(client, crawl, max(0, offset - 1)) if offset else self._lines(client, crawl, 0)
        return [(start, line) for start, line in lines if start >= offset]

    def _locate_za(self, client, crawl, size):
        """Byte range of cluster.idx lines whose blocks can hold ``za,`` keys."""
        def first_at_or_after(key):
            lo, hi = 0, size
            while hi - lo > 4096:
                mid = (lo + hi) // 2
                lines = self._lines(client, crawl, mid)
                if not lines or lines[0][1].split(" ", 1)[0] >= key:
                    hi = mid
                else:
                    lo = mid
            previous, pos = None, lo
            while pos < size:
                lines = self._lines(client, crawl, pos)
                if not lines:
                    break
                for start, line in lines:
                    if line.split(" ", 1)[0] >= key:
                        return (previous if previous is not None else start), start
                    previous = start
                pos = lines[-1][0] + 1  # re-read from inside the last line: its end is now aligned
                if len(lines) == 1 and lines[0][0] + len(lines[0][1].encode()) + 1 >= size:
                    break
            return (previous if previous is not None else size), size

        # The block before the first "za," line can already contain .za captures.
        za_start, _ = first_at_or_after("za,")
        _, za_end = first_at_or_after("za-")
        return za_start, za_end

    def _size(self, client, crawl):
        try:
            response = client.head(DATA + self._index(crawl) + "cluster.idx")
        except httpx.HTTPError:
            raise SourceError("commoncrawl_index_unavailable", 300) from None
        size = int(response.headers.get("content-length") or 0)
        if response.status_code != 200 or not size:
            raise SourceError("commoncrawl_index_unavailable", 300)
        return size

    def _block(self, client, crawl, line):
        parts = line.split("\t")
        if len(parts) < 4:
            return []
        raw = self._range(client, self._index(crawl) + parts[1], int(parts[2]), int(parts[3]))
        if raw is None:
            raise SourceError("commoncrawl_block_unavailable", 120)
        try:
            text = zlib.decompress(raw, 16 + zlib.MAX_WBITS).decode("utf-8", "replace")
        except zlib.error:
            return []
        captures = []
        for row in text.splitlines():
            surt, _, rest = row.partition(" ")
            if not surt.startswith("za,"):
                continue
            _ts, _, payload = rest.partition(" ")
            try:
                item = json.loads(payload)
            except ValueError:
                continue
            if item.get("status") != "200" or "html" not in (item.get("mime-detected") or item.get("mime") or ""):
                continue
            captures.append(item)
        return captures

    @staticmethod
    def _hosts(captures, per_host):
        """Ordered (host, [captures]) with the home page first, then contact/about pages."""
        groups = {}
        for item in captures:
            host = host_of(item.get("url", ""))
            if host.endswith(".za"):
                groups.setdefault(host, []).append(item)
        chosen = []
        for host, items in groups.items():
            def rank(item):
                path = urlsplit(item["url"]).path or "/"
                return (0 if path in {"/", ""} else 1 if CONTACT_PATH.search(path) else 2, len(path))
            picks = [i for i in sorted(items, key=rank) if rank(i)[0] < 2][:per_host]
            if picks:
                chosen.append((host, picks))
        return chosen

    def _page(self, client, item):
        raw = self._range(client, item["filename"], int(item["offset"]), int(item["length"]), attempts=3,
                          missing=b"")
        # Throttled after its retries: body None (retry the host later). Missing or unusable record:
        # b"" (nothing to find, the host is done).
        if raw is None:
            return item, None, None
        return (item, *parse_warc(raw)) if raw else (item, None, b"")

    # ---- collection ---------------------------------------------------------------------------
    def collect(self, cursor, limit):
        cfg, env = rules("sources")["commoncrawl"], settings()
        crawl = cfg["crawl"]
        state = copy.deepcopy(cursor) or {}
        result = Batch(cursor=state)
        if state.get("crawl") != crawl:
            state.clear()
            state["crawl"] = crawl
        if state.get("complete") and not state.get("retry"):
            result.exhausted, result.stop_reason = True, "discovery_complete"
            return result
        deadline = time.monotonic() + max(5, env.job_seconds - 10)
        owned = self.client is None
        client = self.client or httpx.Client(
            timeout=20, headers={"User-Agent": cfg.get("user_agent", "leadgen-shortterm/1.0 (business contact index)")})
        workers = max(1, min(8, int(cfg.get("concurrency", 4))))
        try:
            if "za_start" not in state:
                state["za_start"], state["za_end"] = self._locate_za(client, crawl, self._size(client, crawl))
                state["offset"], state["host"] = state["za_start"], 0
            retry = state.setdefault("retry", [])
            with ThreadPoolExecutor(max_workers=workers) as pool:
                # Hosts throttled in earlier batches go first; each gets RETRY_TRIES attempts in all.
                # Each queued host is tried at most once per batch: failures go to the back.
                pending = len(retry)
                while pending and retry and result.scanned < limit and time.monotonic() < deadline:
                    group, budget = [], limit - result.scanned
                    for entry in retry[: min(pending, workers * 2)]:
                        if len(entry["picks"]) > budget:
                            break
                        group.append(entry)
                        budget -= len(entry["picks"])
                    if not group:
                        break
                    flat = [item for entry in group for item in entry["picks"]]
                    pages = list(pool.map(lambda item: self._page(client, item), flat))
                    result.scanned += len(flat)
                    del retry[: len(group)]
                    pending -= len(group)
                    offset = 0
                    for entry in group:
                        own = pages[offset: offset + len(entry["picks"])]
                        offset += len(entry["picks"])
                        if any(body is None for _item, _stamp, body in own):
                            if entry["tries"] + 1 < RETRY_TRIES:
                                retry.append({**entry, "tries": entry["tries"] + 1})
                            else:
                                result.stop_reason = "retry_exhausted"  # reported, then dropped
                            continue
                        record = self._record(entry["host"], own, cfg)
                        if record:
                            result.records.append(record)
                while result.scanned < limit and time.monotonic() < deadline:
                    if state["offset"] >= state["za_end"]:
                        state["complete"] = True
                        result.exhausted, result.stop_reason = True, "discovery_complete"
                        break
                    try:
                        lines = self._aligned(client, crawl, state["offset"])
                        start, line = lines[0] if lines else (None, None)
                        if line is None or start != state["offset"]:
                            state["complete"] = True  # end of file or misaligned: nothing more to read
                            result.exhausted, result.stop_reason = True, "discovery_complete"
                            break
                        hosts = self._hosts(self._block(client, crawl, line), cfg.get("pages_per_host", 2))
                    except SourceError:
                        if not result.scanned:
                            raise
                        result.stop_reason = "index_backoff"  # keep what this batch already found
                        break
                    finished_block = True
                    index = state["host"]
                    while index < len(hosts):
                        # A group of hosts is fetched concurrently; the cursor only moves past whole
                        # hosts, so a time-boxed batch resumes at the first host it did not finish.
                        group, budget = [], limit - result.scanned
                        while index + len(group) < len(hosts) and len(group) < workers * 2:
                            host, picks = hosts[index + len(group)]
                            if len(picks) > budget:
                                picks = picks[:budget]
                            if not picks:
                                break
                            group.append((host, picks))
                            budget -= len(picks)
                        if not group or time.monotonic() >= deadline:
                            state["host"], finished_block = index, False
                            break
                        flat = [item for _host, picks in group for item in picks]
                        fetched = dict(zip(map(id, flat), pool.map(lambda item: self._page(client, item), flat),
                                           strict=True))
                        result.scanned += len(flat)
                        for host, picks in group:
                            pages = [fetched[id(item)] for item in picks]
                            if any(body is None for _item, _stamp, body in pages):
                                # Throttled after retries: queue the host for a later batch and move on.
                                # Only when the queue is full does the batch stop at this host.
                                if len(retry) >= RETRY_MAX:
                                    state["host"], finished_block = index, False
                                    result.stop_reason = "page_backoff"
                                    break
                                retry.append({"host": host, "picks": [slim(i) for i in picks], "tries": 1})
                                index += 1
                                continue
                            record = self._record(host, pages, cfg)
                            if record:
                                result.records.append(record)
                            index += 1
                        if not finished_block:
                            break
                    if not finished_block:
                        break
                    state["offset"], state["host"] = start + len(line.encode()) + 1, 0
        finally:
            if owned:
                client.close()
        if result.stop_reason not in {"discovery_complete", "index_backoff", "page_backoff", "retry_exhausted"}:
            result.stop_reason = "batch_limit" if result.scanned >= limit else "time_limit"
        return result

    @staticmethod
    def _record(host, pages, cfg):
        emails, url, title, stamp = [], "", "", None
        for item, fetched, body in pages:
            if not body:
                continue
            text = body[:2_000_000].decode("utf-8", "replace")
            found = [e for e in business_emails(text, host, cfg) if e not in emails]
            if found and not url:
                url = item["url"]
            emails += found
            if not title:
                match = TITLE.search(body[:200_000])
                if match:
                    # Any address in a title is removed: only the filtered mailboxes may reach the text.
                    title = clean_label(EMAIL.sub(" ", TAGS.sub(" ", match.group(1).decode("utf-8", "replace"))), 120)
                    title = clean_label(ANY_AT.sub(" ", title), 120)
            stamp = stamp or fetched
        emails = emails[: cfg.get("max_emails_per_host", 3)]
        if not emails:
            return None
        published = stamp or datetime.now(UTC)
        if published.tzinfo is None:
            published = published.replace(tzinfo=UTC)
        name = title or host
        return Record(
            source_record_id=host,
            identity=host,
            text=f"Business website {host} ({name}). Published contact: {', '.join(emails)}",
            url=url,
            published_at=published,
            display_name=name,
            business_name=name,
            urls=[f"https://{host}/"],
        )
