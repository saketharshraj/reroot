"""Policy research over a hard-coded registry of trusted, official sources.

Each request fetches the documents live (PDF or HTML) from allow-listed domains only; if a
government site is down or blocks cloud IPs, the last good copy in Cloud Storage is used and
flagged as cached. Passages are ranked by keyword overlap with the question and handed to
Gemini, which must answer only from them and cite the source.

Add a country by adding a key to SOURCES; keep TRUSTED_DOMAINS strict.
"""

import io
import json
import logging
import math
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from pypdf import PdfReader

from reroot.api.alerts import _bucket

log = logging.getLogger(__name__)

# Only these hosts (or their subdomains) are ever fetched.
TRUSTED_DOMAINS = ("gov.in", "nic.in", "vikaspedia.in", "prsindia.org", "moefcc-gcp.in")

SOURCES: dict[str, list[dict]] = {
    "IN": [
        {"id": "van-rules-2023", "title": "Van (Sanrakshan Evam Samvardhan) Rules, 2023",
         "publisher": "Ministry of Environment, Forest and Climate Change (Gazette copy)",
         "url": "https://www.thc.nic.in/Central%20Governmental%20Rules%5CVan%20(Sanrakshan%20Evam%20Samvardhan)%20Rules,%202023.pdf",
         "topics": "forest clearance diversion compensatory afforestation non-forest land degraded forest land protected forest user agency stage approval land bank rules 2023"},
        {"id": "van-amendment-2024", "title": "Van (Sanrakshan Evam Samvardhan) Amendment Rules, 2024",
         "publisher": "MoEFCC, Forest Conservation Division",
         "url": "https://forestsclearance.nic.in/writereaddata/Rules/The_Van_Amendment_Rules_2024.pdf",
         "topics": "amendment compensatory afforestation degraded forest land twice the area central government agencies psu coal exception"},
        {"id": "caf-act-2016", "title": "Compensatory Afforestation Fund Act, 2016",
         "publisher": "Parliament of India (India Code; PRS mirror)",
         "url": "https://www.indiacode.nic.in/bitstream/123456789/14965/1/campa_act_2016_pdf.pdf",
         "mirrors": ["https://prsindia.org/files/bills_acts/bills_parliament/2015/CAMPA_act,_2016_1.pdf"],
         "topics": "campa fund net present value npv authority state fund national fund money user agency deposit afforestation fund"},
        {"id": "green-credit-summary", "title": "Green Credit Programme: rules and tree-plantation methodology",
         "publisher": "Gujarat Forest Department",
         "url": "https://forests.gujarat.gov.in/writereaddata/images/pdf/GCP-details-09-04-2025.pdf",
         "topics": "green credit programme rules 2023 tree plantation methodology degraded land credits canopy density csr companies icfre"},
        {"id": "green-credit-portal", "title": "Green Credit Programme portal",
         "publisher": "MoEFCC / ICFRE",
         "url": "https://www.moefcc-gcp.in/",
         "topics": "green credit programme registration plantation degraded land parcels companies credits"},
        {"id": "aravalli-green-wall", "title": "Aravalli Green Wall Project",
         "publisher": "Vikaspedia (Government of India)",
         "url": "https://en.vikaspedia.in/energy/policy-support/environment-1/aravalli-green-wall-project",
         "topics": "aravalli green wall haryana rajasthan gujarat delhi buffer desertification restoration degraded land water bodies"},
        {"id": "isfr-2023", "title": "India State of Forest Report 2023",
         "publisher": "Forest Survey of India",
         "url": "https://fsi.nic.in/forest-report-2023",
         "topics": "forest cover tree cover state of forest report statistics carbon stock"},
    ],
}

COUNTRY_NAMES = {"IN": "India"}
_cache: dict[str, tuple[float, dict]] = {}
CACHE_TTL_S = 6 * 3600
STOP = set("the a an of and or to in on for with by is are be as at that this it from what which how do does can under any all".split())


def _trusted(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return any(host == d or host.endswith("." + d) for d in TRUSTED_DOMAINS)


def _extract(content: bytes, content_type: str) -> str:
    if "pdf" in content_type or content[:4] == b"%PDF":
        reader = PdfReader(io.BytesIO(content))
        return "\n\n".join((p.extract_text() or "") for p in reader.pages[:80])
    soup = BeautifulSoup(content, "html.parser")
    nd = soup.find("script", id="__NEXT_DATA__")
    next_data = nd.string if nd else None  # read before the script tags are removed
    for tag in soup(["script", "style", "nav", "header", "footer", "noscript"]):
        tag.decompose()
    text = soup.get_text("\n")
    if len(text.strip()) < 2000 and next_data:
        # Client-rendered pages (e.g. Vikaspedia) ship the article inside Next.js page data.
        strings: list[str] = []

        def walk(v):
            if isinstance(v, str) and len(v) > 60:
                strings.append(BeautifulSoup(v, "html.parser").get_text(" "))
            elif isinstance(v, dict):
                for x in v.values():
                    walk(x)
            elif isinstance(v, list):
                for x in v:
                    walk(x)

        walk(json.loads(next_data))
        text = text + "\n\n" + "\n\n".join(dict.fromkeys(strings))
    return text


def _lenient_session() -> requests.Session:
    import ssl

    import urllib3
    from requests.adapters import HTTPAdapter

    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    class LegacyTLS(HTTPAdapter):
        def init_poolmanager(self, *args, **kwargs):
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            ctx.options |= getattr(ssl, "OP_LEGACY_SERVER_CONNECT", 0x4)
            kwargs["ssl_context"] = ctx
            return super().init_poolmanager(*args, **kwargs)

    sess = requests.Session()
    sess.mount("https://", LegacyTLS())
    return sess


def _snapshot_blob(sid: str):
    return _bucket().blob(f"policy/{sid}.json")


def fetch(source: dict, timeout: float = 8) -> dict:
    """Live fetch (url, then mirrors) with a Cloud Storage snapshot as fallback."""
    hit = _cache.get(source["id"])
    if hit and time.time() - hit[0] < CACHE_TTL_S:
        return hit[1]
    for url in [source["url"], *source.get("mirrors", [])]:
        if not _trusted(url):
            log.warning("refusing untrusted url %s", url)
            continue
        try:
            headers = {"User-Agent": "Mozilla/5.0 (ReRoot policy research)"}
            tls_verified = True
            try:
                r = requests.get(url, timeout=timeout, headers=headers)
            except requests.exceptions.SSLError:
                # Several government servers omit intermediate certificates or need legacy
                # TLS renegotiation. For allow-listed hosts only, retry leniently and flag it.
                r = _lenient_session().get(url, timeout=timeout, headers=headers, verify=False)
                tls_verified = False
            r.raise_for_status()
            text = _extract(r.content, r.headers.get("content-type", ""))
            if len(text.strip()) < 500:
                raise ValueError("no extractable text")
            doc = {"text": text, "fetched_at": datetime.now(timezone.utc).isoformat(), "live": True,
                   "fetched_from": url, "tls_verified": tls_verified}
            try:
                _snapshot_blob(source["id"]).upload_from_string(json.dumps(doc), content_type="application/json")
            except Exception:  # noqa: BLE001 - snapshot is best effort
                pass
            _cache[source["id"]] = (time.time(), doc)
            return doc
        except Exception as e:  # noqa: BLE001
            log.warning("live fetch failed for %s: %s", url, e)
    try:
        doc = json.loads(_snapshot_blob(source["id"]).download_as_bytes()) | {"live": False}
        _cache[source["id"]] = (time.time(), doc)
        return doc
    except Exception:  # noqa: BLE001
        return {"text": "", "live": False, "fetched_at": None, "fetched_from": None, "error": "unavailable"}


def _terms(s: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9]+", s.lower()) if t not in STOP and len(t) > 2]


def _chunks(text: str, size: int = 900, overlap: int = 150) -> list[str]:
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{2,}", "\n", text)
    out, i = [], 0
    while i < len(text):
        out.append(text[i:i + size])
        i += size - overlap
    # Bilingual gazettes: keep passages that are mostly readable English.
    return [c for c in out if sum(ch.isascii() for ch in c) / max(1, len(c)) > 0.85 and len(_terms(c)) > 25]


def _score(q: list[str], chunk: str, df: dict[str, int], n: int) -> float:
    words = _terms(chunk)
    s = 0.0
    for t in set(q):
        tf = words.count(t)
        if tf:
            s += (1 + math.log(tf)) * math.log(1 + n / (1 + df.get(t, 0)))
    # reward phrase matches of adjacent query terms
    low = " ".join(words)
    s += sum(2.0 for a, b in zip(q, q[1:]) if f"{a} {b}" in low)
    return s


def research(question: str, country: str = "IN", max_sources: int = 4, k: int = 6) -> dict:
    sources = SOURCES.get(country.upper())
    if not sources:
        return {"error": f"no trusted policy sources configured for {country}", "available_countries": list(SOURCES)}
    q = _terms(question)
    ranked = sorted(sources, key=lambda s: -sum(_terms(s["topics"] + " " + s["title"]).count(t) for t in q))[:max_sources]
    with ThreadPoolExecutor(max_workers=len(ranked)) as pool:
        docs = list(pool.map(fetch, ranked))

    chunks = [(src, doc, c) for src, doc in zip(ranked, docs) for c in _chunks(doc.get("text", ""))]
    df: dict[str, int] = {}
    for _, _, c in chunks:
        for t in set(_terms(c)):
            df[t] = df.get(t, 0) + 1
    top = sorted(chunks, key=lambda x: -_score(q, x[2], df, len(chunks)))[:k]
    return {
        "question": question,
        "country": COUNTRY_NAMES.get(country.upper(), country),
        "excerpts": [{"source_id": s["id"], "title": s["title"], "text": " ".join(c.split())} for s, _, c in top],
        "sources": [{"id": s["id"], "title": s["title"], "publisher": s["publisher"], "url": d.get("fetched_from") or s["url"],
                     "live": d.get("live", False), "fetched_at": d.get("fetched_at"), "available": bool(d.get("text")),
                     "tls_verified": d.get("tls_verified", True)}
                    for s, d in zip(ranked, docs)],
        "note": "Answer only from these excerpts and cite the source titles. Say so if they don't cover the question.",
    }


def warm_snapshots(country: str = "IN") -> list[tuple[str, bool, int]]:
    """Fetch every source once (e.g. from a machine in India) so cloud servers have a fallback copy."""
    out = []
    for s in SOURCES[country]:
        _cache.pop(s["id"], None)
        d = fetch(s, timeout=60)
        out.append((s["id"], d.get("live", False), len(d.get("text", ""))))
    return out


if __name__ == "__main__":
    for row in warm_snapshots():
        print(row)
