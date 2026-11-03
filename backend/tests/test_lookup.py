"""Tests for the optional Firecrawl name lookup.

No network: every test injects an ``agent`` callable, so the real Firecrawl
client is never contacted.
"""
from __future__ import annotations

import unittest

from bgauto import config, lookup, register, store
from bgauto.web.runs import _web_origins

from support import TempDatabase


class ParseTests(unittest.TestCase):
    def test_parse_matches_normalises_names_and_ids(self):
        data = {
            "matches": [
                {
                    "name": "  HDFC   Bank Limited ",
                    "identifier": "hdfc0000264",
                    "source_url": "https://www.hdfcbank.com/ifsc",
                    "confidence": "high",
                },
                {"name": "HDFC Bank Ltd", "cin": "L74899DL1995PLC070296"},
                {"name": "KL Enterprises LLP", "identifier": "AAA-5012"},
                {"name": "hdfc bank limited"},  # duplicate, ignored
                {"name": ""},  # ignored
            ]
        }
        matches = lookup.parse_matches(data)
        self.assertEqual(matches[0]["name"], "HDFC Bank Limited")
        self.assertEqual(matches[0]["identifier"], "HDFC0000264")
        self.assertEqual(matches[0]["kind"], "IFSC")
        self.assertEqual(matches[1]["identifier"], "L74899DL1995PLC070296")
        self.assertEqual(matches[1]["kind"], "CIN")
        self.assertEqual(matches[2]["identifier"], "AAA-5012")
        self.assertEqual(matches[2]["kind"], "LLPIN")
        self.assertEqual(len(matches), 3)

    def test_parse_accepts_other_shapes_and_caps(self):
        self.assertEqual(
            lookup.parse_matches({"banks": ["ICICI Bank Limited", {"legal_name": "Axis Bank Limited"}]})[1]["name"],
            "Axis Bank Limited",
        )
        self.assertEqual(lookup.parse_matches({"results": ["A", "B", "C"]}, limit=2), [
            {"name": "A", "identifier": "", "kind": "", "source_url": "", "confidence": ""},
            {"name": "B", "identifier": "", "kind": "", "source_url": "", "confidence": ""},
        ])
        self.assertEqual(lookup.parse_matches({"unexpected": "shape"}), [])


class IfscTests(unittest.TestCase):
    TEXT = "\n".join([
        "Beneficiary bank at IFSC: SBIN0017313 , as per the details",
        ":7031: Issuing Branch IFSC",
        "HDFC0000264",
        ":7032: Issuing branch name and address",
        "126, GS ROAD",
        "Another copy HDFC0000264",
    ])

    def test_issuing_code_is_first_and_labelled(self):
        codes = lookup.ifsc_codes(self.TEXT)
        self.assertEqual([item["code"] for item in codes], ["HDFC0000264", "SBIN0017313"])
        self.assertTrue(codes[0]["issuing"])
        self.assertIn("Issuing Branch IFSC", codes[0]["label"])
        self.assertFalse(codes[1]["issuing"])

    def test_only_ifsc_shaped_codes_and_a_cap(self):
        text = "IFSC ABC0123456\nIFSC HDFC0000264\nIFSC ICIC0000123\nIFSC SBIN0000001"
        codes = lookup.ifsc_codes(text, limit=3)
        self.assertEqual([item["code"] for item in codes], ["HDFC0000264", "ICIC0000123", "SBIN0000001"])

    def test_bank_prompt_carries_the_codes(self):
        prompt = lookup.prompt_for("bank", "HDFC Bank Limtied", lookup.ifsc_codes(self.TEXT))
        self.assertIn("HDFC Bank Limtied", prompt)
        self.assertIn("HDFC0000264", prompt)
        self.assertIn("issuing bank", prompt.lower())

    def test_contractor_prompt_is_india_only(self):
        prompt = lookup.prompt_for("contractor", "Trackstar Industries")
        self.assertIn("Trackstar Industries", prompt)
        self.assertIn("Only companies registered in India", prompt)
        self.assertIn("CIN", prompt)


class LookupTests(TempDatabase):
    def setUp(self):
        super().setUp()
        self._saved_key = config.FIRECRAWL_API_KEY
        self._saved_enabled = config.LOOKUP_ENABLED
        config.FIRECRAWL_API_KEY = "fc-test"
        config.LOOKUP_ENABLED = True
        self.addCleanup(self._restore)

    def _restore(self):
        config.FIRECRAWL_API_KEY = self._saved_key
        config.LOOKUP_ENABLED = self._saved_enabled

    def test_top_match_is_chosen_and_duplicate_text_is_cached(self):
        calls = []

        def agent(prompt):
            calls.append(prompt)
            return {
                "matches": [
                    {"name": "HDFC Bank Limited", "identifier": "HDFC0000264"},
                    {"name": "HDFC Bank Ltd"},
                ]
            }

        first = lookup.lookup_field("bank", "HDFC Bank Limtied", IfscTests.TEXT, agent)
        self.assertEqual(first["status"], "ok")
        self.assertEqual(first["chosen"], "HDFC Bank Limited")
        values = {"Bank Name": "HDFC Bank Limtied", "Name of Contractor": ""}
        result = {"bank": first, "contractor": {"status": "skipped", "matches": [], "chosen": ""}}
        lookup.apply(values, result)
        self.assertEqual(values["Bank Name"], "HDFC Bank Limited")
        self.assertEqual(values["Name of Contractor"], "")

        second = lookup.lookup_field("bank", "HDFC Bank Limtied", IfscTests.TEXT, agent)
        self.assertEqual(second["chosen"], "HDFC Bank Limited")
        self.assertTrue(second["cached"])
        self.assertEqual(len(calls), 1)  # cached copy did not call the agent
        # A different IFSC context is a different cache key, so it calls again.
        lookup.lookup_field("bank", "HDFC Bank Limtied", "IFSC ICIC0000123", agent)
        self.assertEqual(len(calls), 2)

    def test_failure_keeps_the_ocr_value(self):
        def agent(prompt):
            raise lookup.LookupFailed("timed out")

        result = lookup.lookup_field("contractor", "Trackstar Industries", "some text", agent)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["matches"], [])
        self.assertEqual(result["chosen"], "Trackstar Industries")
        self.assertIn("timed out", result["error"])

    def test_no_key_means_disabled_and_no_call(self):
        config.FIRECRAWL_API_KEY = ""

        def agent(prompt):  # pragma: no cover - must not run
            raise AssertionError("agent was called without a key")

        result = lookup.lookup_field("bank", "HDFC", IfscTests.TEXT, agent)
        self.assertEqual(result["status"], "disabled")
        self.assertEqual(result["chosen"], "HDFC")

    def test_lookup_entry_runs_both_prompts(self):
        seen = {}

        def agent(prompt):
            if "issuing bank" in prompt.lower():
                seen["bank"] = prompt
                return {"matches": [{"name": "HDFC Bank Limited", "identifier": "HDFC0000264"}]}
            seen["contractor"] = prompt
            return {"matches": [{"name": "National Highways Infra Investment Managers Private Limited",
                                 "identifier": "U45200MH2021PTC000000"}]}

        values = {"Bank Name": "HDFC Bank Limtied", "Name of Contractor": "Trackstar Industries"}
        result = lookup.lookup_entry(values, IfscTests.TEXT, agent)
        lookup.apply(values, result)
        self.assertIn("HDFC0000264", seen["bank"])
        self.assertIn("Trackstar Industries", seen["contractor"])
        self.assertEqual(values["Bank Name"], "HDFC Bank Limited")
        self.assertEqual(values["Name of Contractor"],
                         "National Highways Infra Investment Managers Private Limited")
        self.assertEqual(result["bank"]["chosen"], "HDFC Bank Limited")

    def test_web_names_are_tagged_in_the_masters(self):
        with store.connect() as conn:
            register.ensure_contractor(conn, "Web Contractor Private Limited", origin="web")
            register.ensure_bank(conn, "Web Bank Limited", origin="web")
            contractor = conn.execute(
                "SELECT source FROM contractor WHERE name = ?", ("Web Contractor Private Limited",)
            ).fetchone()["source"]
            bank = conn.execute(
                "SELECT source FROM bank WHERE name = ?", ("Web Bank Limited",)
            ).fetchone()["source"]
        self.assertEqual(contractor, "web")
        self.assertEqual(bank, "web")

    def test_web_origins_only_marks_the_selected_match(self):
        entry = {
            "lookup": {
                "bank": {
                    "status": "ok",
                    "chosen": "HDFC Bank Limited",
                    "matches": [{"name": "HDFC Bank Limited"}, {"name": "HDFC Bank Ltd"}],
                },
                "contractor": {
                    "status": "ok",
                    "chosen": "Web Contractor Private Limited",
                    "matches": [{"name": "Web Contractor Private Limited"}],
                },
            }
        }
        values = {
            "Bank Name": "HDFC Bank Ltd",  # a lower-ranked match the reviewer picked
            "Name of Contractor": "Trackstar Industries",  # reviewer kept the OCR read
        }
        self.assertEqual(_web_origins(entry, values), {"Bank Name": "web"})


if __name__ == "__main__":
    unittest.main()
