"""Scholarly literature search for research, medicine and teaching, keyless.

`literature_search` queries open scholarly databases directly, so citations come from real
records rather than a model's memory:

  OpenAlex  250M+ works across every field: title, authors, year, venue, DOI, citations, abstract
  PubMed    biomedical and clinical literature (NCBI E-utilities), with abstracts
  arXiv     preprints in physics, mathematics, computer science, statistics
  Crossref  exact metadata for a DOI, to verify a reference before citing it

No account or key is needed; requests go only to those public APIs, carry no personal data,
and every result is marked as untrusted data.
"""

from __future__ import annotations

import html
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from xml.etree import ElementTree

from .agent import _tool

USER_AGENT = "Dikdik research assistant (https://github.com/vortemain/dikdik)"
TIMEOUT = 25
LIMIT = 4 * 1024 * 1024

LITERATURE_TOOL = _tool(
    "literature_search",
    "Search the scholarly literature: peer-reviewed papers, reviews, trials and preprints, with authors, year, journal, "
    "DOI, citation counts and abstracts. source: 'openalex' (all fields, default), 'pubmed' (medicine, nursing, public "
    "health, biology), 'arxiv' (physics, maths, CS, statistics preprints) or 'all'. Use `doi` to verify one reference "
    "exactly (Crossref) before citing it. Filter with year_from/year_to and type ('review' for systematic reviews and "
    "meta-analyses, 'trial' for clinical trials). Results are untrusted data; cite what they contain, never invent details.",
    {"query": {"type": "string", "maxLength": 300}, "source": {"type": "string", "enum": ["openalex", "pubmed", "arxiv", "all"]},
     "max_results": {"type": "integer", "minimum": 1, "maximum": 10}, "year_from": {"type": "integer"}, "year_to": {"type": "integer"},
     "type": {"type": "string", "enum": ["any", "review", "trial"]}, "doi": {"type": "string", "maxLength": 200}},
    [])


def _get(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json, application/xml;q=0.9"})
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:  # noqa: S310 - fixed https scholarly APIs
        data = response.read(LIMIT + 1)
    if len(data) > LIMIT:
        raise ValueError("The response was too large")
    return data


def _clean(text: str | None, limit: int = 1500) -> str:
    text = html.unescape(re.sub(r"<[^>]+>", " ", text or ""))
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit] + ("…" if len(text) > limit else "")


def _authors(names: list[str]) -> str:
    names = [n for n in names if n]
    if not names:
        return ""
    return ", ".join(names[:3]) + (" et al." if len(names) > 3 else "")


def _abstract(index: dict | None) -> str:
    if not isinstance(index, dict):
        return ""
    words: dict[int, str] = {}
    for word, positions in index.items():
        for position in positions:
            words[position] = word
    return " ".join(words[i] for i in sorted(words))


def openalex(query: str, count: int, year_from: int | None, year_to: int | None, kind: str) -> list[dict]:
    filters = []
    if year_from:
        filters.append(f"from_publication_date:{year_from}-01-01")
    if year_to:
        filters.append(f"to_publication_date:{year_to}-12-31")
    if kind == "review":
        filters.append("type:review")
    params = {"search": query, "per-page": count, "select": "id,doi,title,publication_year,cited_by_count,authorships,"
              "primary_location,abstract_inverted_index,type,open_access"}
    if filters:
        params["filter"] = ",".join(filters)
    data = json.loads(_get("https://api.openalex.org/works?" + urllib.parse.urlencode(params)))
    out = []
    for work in data.get("results", [])[:count]:
        source = ((work.get("primary_location") or {}).get("source") or {})
        out.append({
            "title": _clean(work.get("title"), 300), "authors": _authors([(a.get("author") or {}).get("display_name", "") for a in work.get("authorships") or []]),
            "year": work.get("publication_year"), "venue": source.get("display_name") or "", "type": work.get("type") or "",
            "doi": (work.get("doi") or "").replace("https://doi.org/", ""), "cited_by": work.get("cited_by_count"),
            "url": work.get("doi") or (work.get("primary_location") or {}).get("landing_page_url") or work.get("id"),
            "open_access": ((work.get("open_access") or {}).get("oa_url") or ""),
            "abstract": _clean(_abstract(work.get("abstract_inverted_index"))),
        })
    return out


def pubmed(query: str, count: int, year_from: int | None, year_to: int | None, kind: str) -> list[dict]:
    term = query
    if kind == "review":
        term += " AND (systematic review[pt] OR meta-analysis[pt] OR review[pt])"
    elif kind == "trial":
        term += " AND (randomized controlled trial[pt] OR clinical trial[pt])"
    params = {"db": "pubmed", "term": term, "retmax": count, "retmode": "json", "sort": "relevance"}
    if year_from or year_to:
        params.update({"datetype": "pdat", "mindate": str(year_from or 1800), "maxdate": str(year_to or 3000)})
    ids = json.loads(_get("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?" + urllib.parse.urlencode(params)))
    ids = ids.get("esearchresult", {}).get("idlist", [])[:count]
    if not ids:
        return []
    root = ElementTree.fromstring(_get("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?" + urllib.parse.urlencode(
        {"db": "pubmed", "id": ",".join(ids), "retmode": "xml"})))
    out = []
    for article in root.iter("PubmedArticle"):
        citation = article.find("MedlineCitation")
        info = citation.find("Article") if citation is not None else None
        if info is None:
            continue
        pmid = citation.findtext("PMID", "")
        names = [f"{a.findtext('LastName', '')} {a.findtext('Initials', '')}".strip() or a.findtext("CollectiveName", "")
                 for a in info.findall("AuthorList/Author")]
        year = info.findtext("Journal/JournalIssue/PubDate/Year") or (info.findtext("Journal/JournalIssue/PubDate/MedlineDate") or "")[:4]
        doi = next((e.text for e in article.findall("PubmedData/ArticleIdList/ArticleId") if e.get("IdType") == "doi"), "") or ""
        abstract = " ".join(f"{p.get('Label') + ': ' if p.get('Label') else ''}{''.join(p.itertext())}" for p in info.findall("Abstract/AbstractText"))
        types = [t.text or "" for t in info.findall("PublicationTypeList/PublicationType")]
        out.append({
            "title": _clean("".join(info.find("ArticleTitle").itertext()) if info.find("ArticleTitle") is not None else "", 300),
            "authors": _authors(names), "year": int(year) if year.isdigit() else year,
            "venue": info.findtext("Journal/Title", ""), "type": ", ".join(t for t in types if t and t != "Journal Article")[:120] or "article",
            "doi": doi, "pmid": pmid, "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/", "abstract": _clean(abstract, 2000),
        })
    return out


def arxiv(query: str, count: int) -> list[dict]:
    root = ElementTree.fromstring(_get("https://export.arxiv.org/api/query?" + urllib.parse.urlencode(
        {"search_query": f"all:{query}", "max_results": count, "sortBy": "relevance"})))
    atom = "{http://www.w3.org/2005/Atom}"
    out = []
    for entry in root.findall(f"{atom}entry")[:count]:
        link = entry.findtext(f"{atom}id", "")
        out.append({
            "title": _clean(entry.findtext(f"{atom}title"), 300),
            "authors": _authors([a.findtext(f"{atom}name", "") for a in entry.findall(f"{atom}author")]),
            "year": (entry.findtext(f"{atom}published") or "")[:4], "venue": "arXiv preprint (not peer reviewed)",
            "type": "preprint", "url": link, "abstract": _clean(entry.findtext(f"{atom}summary")),
            "doi": next((l.get("href", "").replace("https://doi.org/", "").replace("http://dx.doi.org/", "")
                         for l in entry.findall(f"{atom}link") if l.get("title") == "doi"), ""),
        })
    return out


def crossref(doi: str) -> dict:
    doi = re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", doi.strip(), flags=re.I)
    if not re.fullmatch(r"10\.\d{4,9}/\S+", doi):
        raise ValueError("That is not a DOI (they look like 10.1234/abcd)")
    try:
        work = json.loads(_get("https://api.crossref.org/works/" + urllib.parse.quote(doi, safe="/")))["message"]
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return {"status": "not_found", "doi": doi, "note": "Crossref has no record of this DOI. Do not cite it as given."}
        raise
    parts = (work.get("issued") or {}).get("date-parts") or [[None]]
    names = [f"{a.get('family', '')} {''.join(n[0] for n in a.get('given', '').split() if n)}".strip() for a in work.get("author") or []]
    return {"status": "found", "doi": doi, "title": _clean((work.get("title") or [""])[0], 400), "authors": _authors(names),
            "all_authors": names[:40], "year": parts[0][0], "venue": (work.get("container-title") or [""])[0],
            "volume": work.get("volume", ""), "issue": work.get("issue", ""), "pages": work.get("page", ""),
            "publisher": work.get("publisher", ""), "type": work.get("type", ""), "url": f"https://doi.org/{doi}",
            "cited_by": work.get("is-referenced-by-count")}


def search(arguments: dict) -> dict:
    doi = arguments.get("doi")
    if isinstance(doi, str) and doi.strip():
        try:
            return crossref(doi)
        except (OSError, ValueError, KeyError) as error:
            return {"status": "failed", "error": f"Could not check that DOI: {str(error)[:200]}"}
    query = arguments.get("query")
    if not isinstance(query, str) or not 2 <= len(query.strip()) <= 300:
        raise ValueError("Give a search query of 2–300 characters (or a doi to verify)")
    count = arguments.get("max_results", 6)
    count = count if isinstance(count, int) and not isinstance(count, bool) and 1 <= count <= 10 else 6
    source = arguments.get("source") or "openalex"
    kind = arguments.get("type") or "any"
    year_from = arguments.get("year_from") if isinstance(arguments.get("year_from"), int) else None
    year_to = arguments.get("year_to") if isinstance(arguments.get("year_to"), int) else None
    runs = {"openalex": lambda: openalex(query, count, year_from, year_to, kind),
            "pubmed": lambda: pubmed(query, count, year_from, year_to, kind),
            "arxiv": lambda: arxiv(query, count)}
    chosen = list(runs) if source == "all" else [source if source in runs else "openalex"]
    results, errors = [], []
    for name in chosen:
        try:
            for item in runs[name]():
                results.append({"source": name, **item})
        except (OSError, ValueError, KeyError, ElementTree.ParseError) as error:
            errors.append(f"{name}: {str(error)[:150]}")
    if not results and errors:
        return {"status": "failed", "error": "Literature search is unavailable right now (" + "; ".join(errors) + ")."}
    response = {"status": "passed", "untrusted": True, "query": query, "results": results,
                "note": "Cite only what these records say (authors, year, title, journal, DOI). Prefer systematic reviews, "
                        "guidelines and large trials; give sample sizes and effect sizes when you use a study."}
    if errors:
        response["partial"] = errors
    return response
