"""Fixture-based tests for the OSM adapter. No live Nominatim/Overpass calls.

The Nominatim and Overpass fixtures below are trimmed real responses,
captured manually via Postman for Melbourne, VIC, Australia.
"""

import unittest
from unittest.mock import patch

import httpx

from scout.osm_discovery import BoundingBox, discover_domains, geocode_area, parse_to_domains, query_overpass


class StubClient:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def get(self, url, params=None):
        self.calls.append(("GET", url, params))
        if self.error:
            raise self.error
        return self.response

    def post(self, url, content=None, headers=None):
        self.calls.append(("POST", url, content))
        if self.error:
            raise self.error
        return self.response


def json_response(url, method="GET", status=200, payload=None):
    return httpx.Response(status, json=payload, request=httpx.Request(method, url))


NOMINATIM_FIXTURE = [
    {
        "display_name": "Melbourne, Victoria, Australia",
        "boundingbox": ["-38.4993700", "-37.4017500", "144.4440500", "146.1925000"],
    }
]

OVERPASS_FIXTURE = {
    "elements": [
        {
            "type": "node",
            "id": 13605581488,
            "tags": {
                "name": "SSW Melbourne",
                "office": "consulting",
                "consulting": "software",
                "website": "https://www.ssw.com.au/offices/melbourne",
            },
        },
        {
            "type": "node",
            "id": 7121788915,
            "tags": {
                "name": "Expo Centric Pty Ltd - Melbourne",
                "office": "company",
                "contact:website": "http://www.expocentric.com.au/",
            },
        },
        {
            "type": "node",
            "id": 645571437,
            "tags": {"name": "Bupa", "office": "insurance", "website": "https://www.bupa.com.au/"},
        },
        {
            "type": "node",
            "id": 99999999,
            "tags": {"name": "No Website Listed", "office": "company"},
        },
    ]
}


class GeocodeAreaTests(unittest.TestCase):
    @patch("scout.osm_discovery.time.sleep")
    @patch("scout.osm_discovery.httpx.Client")
    def test_returns_bounding_box_from_first_result(self, client_factory, _sleep):
        client_factory.return_value = StubClient(json_response("https://nominatim.openstreetmap.org/search", payload=NOMINATIM_FIXTURE))

        bbox = geocode_area("Melbourne", "VIC", "Australia")

        self.assertEqual(bbox, BoundingBox(south=-38.49937, west=144.44405, north=-37.40175, east=146.1925))

    @patch("scout.osm_discovery.time.sleep")
    @patch("scout.osm_discovery.httpx.Client")
    def test_no_results_returns_none(self, client_factory, _sleep):
        client_factory.return_value = StubClient(json_response("https://nominatim.openstreetmap.org/search", payload=[]))

        self.assertIsNone(geocode_area("Nowhere", "XX", "Nowhereland"))

    @patch("scout.osm_discovery.time.sleep")
    @patch("scout.osm_discovery.httpx.Client")
    def test_server_error_returns_none(self, client_factory, _sleep):
        client_factory.return_value = StubClient(json_response("https://nominatim.openstreetmap.org/search", status=500, payload={}))

        self.assertIsNone(geocode_area("Melbourne", "VIC", "Australia"))

    @patch("scout.osm_discovery.time.sleep")
    @patch("scout.osm_discovery.httpx.Client")
    def test_network_error_returns_none(self, client_factory, _sleep):
        client_factory.return_value = StubClient(error=httpx.ConnectError("offline"))

        self.assertIsNone(geocode_area("Melbourne", "VIC", "Australia"))


class QueryOverpassTests(unittest.TestCase):
    def setUp(self):
        self.bbox = BoundingBox(south=-37.825, west=144.95, north=-37.805, east=144.97)

    @patch("scout.osm_discovery.time.sleep")
    @patch("scout.osm_discovery.httpx.Client")
    def test_returns_elements_on_success(self, client_factory, _sleep):
        # No profile_id -> the 3-tag-pair fallback set, which fits inside one
        # _BATCH_SIZE=4 batch - a single request, single fixture return.
        client_factory.return_value = StubClient(json_response("https://overpass-api.de/api/interpreter", method="POST", payload=OVERPASS_FIXTURE))

        elements = query_overpass(self.bbox)

        self.assertEqual(len(elements), 4)

    @patch("scout.osm_discovery.time.sleep")
    @patch("scout.osm_discovery.httpx.Client")
    def test_one_batchs_request_failing_does_not_blank_out_the_others(self, client_factory, _sleep):
        """Splitting into per-batch requests means a single failing batch
        doesn't take the rest down with it. Uses the technology profile (16
        tag pairs -> 4 batches of _BATCH_SIZE=4) since the 3-tag-pair
        fallback set fits in a single batch and can't exercise this."""
        stub = StubClient(json_response("https://overpass-api.de/api/interpreter", method="POST", payload=OVERPASS_FIXTURE))
        client_factory.return_value = stub
        real_post = stub.post
        calls = {"n": 0}

        def flaky_post(url, content=None, headers=None):
            calls["n"] += 1
            if calls["n"] == 1:
                raise httpx.ReadTimeout("timed out")
            return real_post(url, content=content, headers=headers)

        stub.post = flaky_post

        elements = query_overpass(self.bbox, profile_id="technology")

        self.assertEqual(len(elements), 12)  # 3 successful batches x 4 elements each

    @patch("scout.osm_discovery.time.sleep")
    @patch("scout.osm_discovery.httpx.Client")
    def test_server_error_returns_empty_list(self, client_factory, _sleep):
        client_factory.return_value = StubClient(json_response("https://overpass-api.de/api/interpreter", method="POST", status=504, payload={}))

        self.assertEqual(query_overpass(self.bbox), [])

    @patch("scout.osm_discovery.time.sleep")
    @patch("scout.osm_discovery.httpx.Client")
    def test_network_error_returns_empty_list(self, client_factory, _sleep):
        client_factory.return_value = StubClient(error=httpx.ConnectError("offline"))

        self.assertEqual(query_overpass(self.bbox), [])

    @patch("scout.osm_discovery.time.sleep")
    @patch("scout.osm_discovery.httpx.Client")
    def test_no_profile_falls_back_to_unfiltered_office_plus_amenities_not_university(self, client_factory, _sleep):
        stub = StubClient(json_response("https://overpass-api.de/api/interpreter", method="POST", payload={"elements": []}))
        client_factory.return_value = stub

        query_overpass(self.bbox)

        # 3 tag pairs (unfiltered office + 2 universal amenities) fit in one _BATCH_SIZE=4 batch.
        self.assertEqual(len(stub.calls), 1)
        sent_queries = [call[2] for call in stub.calls]
        self.assertTrue(any('"office"]' in query for query in sent_queries))
        self.assertTrue(any('"amenity"="library"' in query for query in sent_queries))
        self.assertTrue(any('"amenity"="research_institute"' in query for query in sent_queries))
        self.assertTrue(all("university" not in query for query in sent_queries))

    @patch("scout.osm_discovery.time.sleep")
    @patch("scout.osm_discovery.httpx.Client")
    def test_unrecognised_profile_also_falls_back_to_unfiltered_office(self, client_factory, _sleep):
        stub = StubClient(json_response("https://overpass-api.de/api/interpreter", method="POST", payload={"elements": []}))
        client_factory.return_value = stub

        query_overpass(self.bbox, profile_id="not-a-real-profile")

        sent_queries = [call[2] for call in stub.calls]
        self.assertTrue(any('"office"]' in query for query in sent_queries))

    @patch("scout.osm_discovery.time.sleep")
    @patch("scout.osm_discovery.httpx.Client")
    def test_technology_profile_narrows_to_its_own_tags_and_amenities(self, client_factory, _sleep):
        stub = StubClient(json_response("https://overpass-api.de/api/interpreter", method="POST", payload={"elements": []}))
        client_factory.return_value = stub

        query_overpass(self.bbox, profile_id="technology")

        sent_queries = [call[2] for call in stub.calls]
        self.assertTrue(any('"office"="it"' in query for query in sent_queries))
        self.assertTrue(any('"office"="research"' in query for query in sent_queries))
        self.assertTrue(any('"shop"="computer"' in query for query in sent_queries))
        self.assertTrue(any('"amenity"="library"' in query for query in sent_queries))
        # Narrowed: the unfiltered `["office"]` wildcard (no `=value`) must be gone.
        self.assertTrue(all('"office"]' not in query for query in sent_queries))
        # office=software confirmed 0 Melbourne hits (RESEARCH.md) and dropped.
        self.assertTrue(all('"office"="software"' not in query for query in sent_queries))
        # Each request stays one batch of up to _BATCH_SIZE=4 tag pairs' clauses,
        # not everything combined (the original failure: a 68-clause single
        # request timed out live, 2026-08-21; the live-trial-confirmed batch
        # size lives in _BATCH_SIZE's own comment, 2026-08-29).
        self.assertTrue(all(query.count("[out:json]") == 1 for query in sent_queries))
        self.assertEqual(len(stub.calls), 4)  # ceil(16 tag pairs / _BATCH_SIZE=4)


class ParseToDomainsTests(unittest.TestCase):
    def test_extracts_normalised_deduplicated_domains(self):
        domains = parse_to_domains(OVERPASS_FIXTURE["elements"])

        self.assertEqual(
            domains,
            ["https://www.ssw.com.au", "http://www.expocentric.com.au", "https://www.bupa.com.au"],
        )

    def test_skips_elements_without_a_website(self):
        self.assertEqual(parse_to_domains([{"tags": {"name": "No Website"}}]), [])

    def test_skips_elements_without_tags(self):
        self.assertEqual(parse_to_domains([{"type": "node", "id": 1}]), [])

    def test_extracts_domains_from_library_and_research_institute_elements(self):
        elements = [
            {"type": "node", "id": 1, "tags": {"name": "State Library", "amenity": "library", "website": "https://www.slv.vic.gov.au/"}},
            {"type": "node", "id": 2, "tags": {"name": "Murdoch Children's Research Institute", "amenity": "research_institute", "website": "https://www.mcri.edu.au/"}},
        ]

        self.assertEqual(parse_to_domains(elements), ["https://www.slv.vic.gov.au", "https://www.mcri.edu.au"])


class DiscoverDomainsTests(unittest.TestCase):
    @patch("scout.osm_discovery.query_overpass")
    @patch("scout.osm_discovery.geocode_area")
    def test_chains_geocode_and_query(self, geocode, query):
        geocode.return_value = BoundingBox(south=-37.825, west=144.95, north=-37.805, east=144.97)
        query.return_value = OVERPASS_FIXTURE["elements"]

        domains = discover_domains("Melbourne", "VIC", "Australia", "technology")

        self.assertIn("https://www.ssw.com.au", domains)
        query.assert_called_once_with(geocode.return_value, "technology")

    @patch("scout.osm_discovery.query_overpass")
    @patch("scout.osm_discovery.geocode_area")
    def test_no_bbox_skips_overpass_and_returns_empty_list(self, geocode, query):
        geocode.return_value = None

        self.assertEqual(discover_domains("Nowhere", "XX", "Nowhereland"), [])
        query.assert_not_called()


if __name__ == "__main__":
    unittest.main()
