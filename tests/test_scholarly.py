"""literature_search parses OpenAlex, PubMed, arXiv and Crossref records (recorded responses; no network)."""

import json
import unittest
import urllib.error
from unittest.mock import patch

from dikdik import scholarly

OPENALEX = {"results": [{
    "id": "https://openalex.org/W1", "doi": "https://doi.org/10.1016/s0140-6736(01)05783-x",
    "title": "Estimated risk of HIV transmission by blood transfusion in Kenya", "publication_year": 2001, "cited_by_count": 141,
    "type": "article", "authorships": [{"author": {"display_name": n}} for n in ("A Moore", "G Herrera", "J Nyamongo", "E Lackritz")],
    "primary_location": {"source": {"display_name": "The Lancet"}, "landing_page_url": "https://example.org"},
    "open_access": {"oa_url": ""}, "abstract_inverted_index": {"Blood": [0], "safety": [1], "matters.": [2]},
}]}

PUBMED_SEARCH = {"esearchresult": {"idlist": ["38000001"]}}
PUBMED_FETCH = b"""<?xml version="1.0"?><PubmedArticleSet><PubmedArticle><MedlineCitation><PMID>38000001</PMID><Article>
<Journal><Title>The Lancet</Title><JournalIssue><PubDate><Year>2024</Year></PubDate></JournalIssue></Journal>
<ArticleTitle>Safety and efficacy of malaria vaccine candidate R21/Matrix-M</ArticleTitle>
<Abstract><AbstractText Label="BACKGROUND">Malaria remains a leading cause of death.</AbstractText><AbstractText Label="FINDINGS">Efficacy was 75%.</AbstractText></Abstract>
<AuthorList><Author><LastName>Datoo</LastName><Initials>MS</Initials></Author></AuthorList>
<PublicationTypeList><PublicationType>Journal Article</PublicationType><PublicationType>Randomized Controlled Trial</PublicationType></PublicationTypeList>
</Article></MedlineCitation><PubmedData><ArticleIdList><ArticleId IdType="doi">10.1016/S0140-6736(23)02511-4</ArticleId></ArticleIdList></PubmedData></PubmedArticle></PubmedArticleSet>"""

ARXIV = b"""<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><entry><id>http://arxiv.org/abs/1706.03762v7</id>
<published>2017-06-12T00:00:00Z</published><title>Attention Is All You Need</title><summary>The dominant sequence models...</summary>
<author><name>Ashish Vaswani</name></author></entry></feed>"""

CROSSREF = {"message": {"title": ["Highly accurate protein structure prediction with AlphaFold"], "issued": {"date-parts": [[2021, 7, 15]]},
                        "author": [{"given": "John", "family": "Jumper"}], "container-title": ["Nature"], "volume": "596",
                        "page": "583-589", "publisher": "Springer", "type": "journal-article", "is-referenced-by-count": 30000}}


def fake_get(url):
    if "openalex" in url:
        return json.dumps(OPENALEX).encode()
    if "esearch" in url:
        return json.dumps(PUBMED_SEARCH).encode()
    if "efetch" in url:
        return PUBMED_FETCH
    if "arxiv" in url:
        return ARXIV
    if "crossref" in url:
        if "made-up" in url:
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
        return json.dumps(CROSSREF).encode()
    raise AssertionError(url)


class ScholarlyTests(unittest.TestCase):
    def test_openalex_records_become_citable_results(self):
        with patch("dikdik.scholarly._get", side_effect=fake_get):
            result = scholarly.search({"query": "blood transfusion Kenya"})
        record = result["results"][0]
        self.assertEqual(result["status"], "passed")
        self.assertEqual(record["authors"], "A Moore, G Herrera, J Nyamongo et al.")
        self.assertEqual(record["doi"], "10.1016/s0140-6736(01)05783-x")
        self.assertEqual((record["year"], record["venue"], record["cited_by"]), (2001, "The Lancet", 141))
        self.assertEqual(record["abstract"], "Blood safety matters.")

    def test_pubmed_gives_structured_abstracts_and_trial_types(self):
        with patch("dikdik.scholarly._get", side_effect=fake_get) as get:
            result = scholarly.search({"query": "R21 malaria vaccine", "source": "pubmed", "type": "trial", "year_from": 2020})
        record = result["results"][0]
        self.assertIn("randomized controlled trial[pt]", get.call_args_list[0].args[0].replace("+", " ").replace("%5B", "[").replace("%5D", "]"))
        self.assertEqual(record["pmid"], "38000001")
        self.assertEqual(record["doi"], "10.1016/S0140-6736(23)02511-4")
        self.assertIn("FINDINGS: Efficacy was 75%.", record["abstract"])
        self.assertEqual(record["type"], "Randomized Controlled Trial")

    def test_arxiv_preprints_are_labelled_as_not_peer_reviewed(self):
        with patch("dikdik.scholarly._get", side_effect=fake_get):
            record = scholarly.search({"query": "attention", "source": "arxiv"})["results"][0]
        self.assertEqual(record["title"], "Attention Is All You Need")
        self.assertIn("not peer reviewed", record["venue"])

    def test_doi_verification_finds_real_and_rejects_invented_references(self):
        with patch("dikdik.scholarly._get", side_effect=fake_get):
            real = scholarly.search({"doi": "https://doi.org/10.1038/s41586-021-03819-2"})
            fake = scholarly.search({"doi": "10.9999/made-up-reference"})
            bad = scholarly.search({"doi": "not a doi"})
        self.assertEqual((real["status"], real["venue"], real["year"]), ("found", "Nature", 2021))
        self.assertEqual(fake["status"], "not_found")
        self.assertIn("Do not cite", fake["note"])
        self.assertEqual(bad["status"], "failed")

    def test_all_sources_and_failures_are_reported(self):
        with patch("dikdik.scholarly._get", side_effect=fake_get):
            combined = scholarly.search({"query": "anything", "source": "all", "max_results": 2})
        self.assertEqual({r["source"] for r in combined["results"]}, {"openalex", "pubmed", "arxiv"})
        with patch("dikdik.scholarly._get", side_effect=OSError("offline")):
            self.assertEqual(scholarly.search({"query": "anything"})["status"], "failed")
        with self.assertRaises(ValueError):
            scholarly.search({"query": "x"})


if __name__ == "__main__":
    unittest.main()
