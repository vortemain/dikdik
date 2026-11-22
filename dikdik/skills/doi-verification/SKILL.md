---
name: doi-verification
description: Check that a DOI exists and matches the cited paper (title, authors, year, journal) before citing it, one at a time or a whole reference list in run_code.
category: research
version: "1.0"
type: recipe
triggers: "verify doi, check doi, check references, reference list, citation check"
---

# Verifying DOIs

A DOI that resolves is not enough: it must point to the paper the citation names.
Fabricated or mistyped DOIs often resolve to a different paper.

## One DOI

1. `literature_search` with the DOI as the query returns the Crossref record, or
   `web_fetch https://api.crossref.org/works?filter=doi:<doi>&select=DOI,title,author,issued,container-title`
   (the full `/works/<doi>` record lists every reference and can be cut off).
2. Compare the record's title, first author's family name, year and journal with
   the citation. Small differences (capitalisation, subtitle, online-first year)
   are fine; a different title or author is not.
3. No Crossref record? Ask the DOI handle service, which knows every registration
   agency (Crossref, DataCite, mEDRA):
   `https://doi.org/api/handles/<doi>` — `responseCode` 1 means the DOI exists, 100
   means it does not. A DataCite DOI's metadata is at `https://api.datacite.org/dois/<doi>`.

## A whole reference list

Use `run_code` (Python) so the checking is real code, not reading:

```python
from dikdik_tools import web_fetch
import json, difflib

references = [
    ("10.1016/S0140-6736(21)00943-0", "Efficacy of a malaria vaccine ..."),
    # (doi, title as cited)
]
for doi, cited in references:
    reply = web_fetch("https://api.crossref.org/works?select=DOI,title,author,issued,container-title&filter=doi:" + doi)
    try:
        work = json.loads(reply["text"])["message"]["items"][0]
    except (TypeError, ValueError, KeyError, IndexError):
        print("NOT FOUND", doi)
        continue
    title = (work.get("title") or [""])[0]
    score = difflib.SequenceMatcher(None, title.lower(), cited.lower()).ratio()
    year = (work.get("issued", {}).get("date-parts") or [[None]])[0][0]
    print("OK  " if score > 0.85 else "DIFF", doi, year, round(score, 2), title[:80])
```

Extract the DOIs from a document first with `read_document` (or `find: "doi.org"`),
normalising them: strip `https://doi.org/`, `doi:` and trailing punctuation, and
lowercase them for comparison (DOIs are case-insensitive).

## Report

List each reference as verified, mismatched (say what differs and the correct
record) or not found. Never "fix" a citation by inventing a DOI: give the record
you found, or say none was found.
