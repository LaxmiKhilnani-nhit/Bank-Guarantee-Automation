"""Fast regression tests: parsers and field logic, no OCR involved.

Run from the repo root:

    $env:PYTHONPATH = "backend"
    .venv\\Scripts\\python.exe -m unittest discover -s backend/tests -v

The text below is synthetic and mirrors the shapes seen on the two sample
guarantees; no BG data from the real tracker is stored here.
"""
from __future__ import annotations

import datetime as dt
import unittest
from pathlib import Path

from bgauto.extract import _bg_no, _flatten, _loa, _po, extract, extract_from_pipeline
from bgauto.masters import Masters
from bgauto.normalize import (
    amount_in_words_to_number,
    bg_no_from_filename,
    match_bg_no_hint,
    normalise_bg_no,
    parse_amount,
    parse_date,
)
from bgauto.ocr import DocumentOCR, PageOCR


def _doc(*pages: list[str]) -> DocumentOCR:
    return DocumentOCR(
        source=Path("BG 1234AB12345678901.pdf"),
        pages=[
            PageOCR(number=i, width=0, height=0, text="\n".join(lines))
            for i, lines in enumerate(pages, start=1)
        ],
    )


class ParseDateTests(unittest.TestCase):
    def test_dash_month(self):
        self.assertEqual(parse_date("15-SEP-2025"), dt.date(2025, 9, 15))

    def test_ocr_zero_in_month(self):
        self.assertEqual(parse_date("11-0CT-2028"), dt.date(2028, 10, 11))

    def test_numeric(self):
        self.assertEqual(parse_date("06.04.2026"), dt.date(2026, 4, 6))

    def test_month_name(self):
        self.assertEqual(parse_date("Issuance Date: November 15, 2025"), dt.date(2025, 11, 15))

    def test_no_date(self):
        self.assertIsNone(parse_date("not a date"))


class ParseAmountTests(unittest.TestCase):
    def test_lakh_grouping(self):
        self.assertEqual(parse_amount("GUARANTEE AMOUNT : INR 46,99,752.00"), 4699752.0)

    def test_spaces_in_number(self):
        self.assertEqual(parse_amount("INR 40, 78,080.00"), 4078080.0)

    def test_not_money(self):
        self.assertIsNone(parse_amount("no amount here"))


class AmountInWordsTests(unittest.TestCase):
    def test_lakh_and_thousand(self):
        self.assertEqual(
            amount_in_words_to_number("RUPEES FORTY LAKH SEVENTY EIGHT THOUSAND EIGHTY ONLY"),
            4078080,
        )

    def test_unknown_word_refuses(self):
        self.assertIsNone(amount_in_words_to_number("RUPEES FORTY WIDGETS ONLY"))


class BgNoTests(unittest.TestCase):
    def test_clean(self):
        self.assertEqual(normalise_bg_no("264GT02252580004"), "264GT02252580004")

    def test_ambiguous_token_alone_is_refused(self):
        # OCR lost the G: could be 2646T... or 2646TO... - never guess.
        self.assertIsNone(normalise_bg_no("2646T02252580004"))

    def test_filename_hint_resolves_ambiguous_token(self):
        self.assertEqual(
            match_bg_no_hint("2646T02252580004", "264GT02252580004"),
            "264GT02252580004",
        )

    def test_filename_hint_rejects_different_number(self):
        self.assertIsNone(match_bg_no_hint("999GT02252580004", "264GT02252580004"))

    def test_reject_short(self):
        self.assertIsNone(normalise_bg_no("123"))

    def test_filename_hint(self):
        self.assertEqual(bg_no_from_filename("BG  264GT02252580004"), "264GT02252580004")


class BgNoFromDocumentTests(unittest.TestCase):
    def test_company_cin_is_not_a_bg_no(self):
        doc = _doc([
            "Company CIN: 1994PLC021012",
            "OUR REFERENCE: 264GT02252580004",
        ])
        result = extract(doc, Masters.load())
        self.assertEqual(result.value("BG No"), "264GT02252580004")

    def test_hint_beats_damaged_token(self):
        doc = DocumentOCR(
            source=Path("BG  264GT02252580004.pdf"),
            pages=[PageOCR(number=1, width=0, height=0, text="OUR REFERENCE > 2646T02252580004")],
        )
        field = _bg_no(doc, [(1, "OUR REFERENCE > 2646T02252580004")])
        self.assertEqual(field.value, "264GT02252580004")
        self.assertIn("bg_no_repaired_from_filename", field.notes)
        self.assertEqual(field.flags, [])


class FlattenTests(unittest.TestCase):
    def test_margin_numbers_removed(self):
        lines = [
            (1, "11 into an agreement (hereinafter called the Agreement) WO No NSSPL/FY25-26/Bridge Repair &"),
            (1, "12 Rehabilitation - Bareilly - Sitapur"),
        ]
        self.assertEqual(
            _flatten(lines),
            "into an agreement (hereinafter called the Agreement) WO No NSSPL/FY25-26/Bridge Repair & "
            "Rehabilitation - Bareilly - Sitapur",
        )

    def test_work_order_is_po_and_drops_line_numbers_not_loa(self):
        lines = [
            (1, "11 into an agreement, WO No NSSPL/FY25-26/Bridae Repair &"),
            (1, "12 Rehabilitation - Bareilly - Sitapur\u201d for the [project]"),
        ]
        loa = _loa(lines)
        po = _po(lines)
        self.assertIsNone(loa.value)
        self.assertEqual(po.value, "NSSPL/FY25-26/Bridge Repair & Rehabilitation - Bareilly - Sitapur")
        self.assertEqual(po.flags, [])

    def test_letter_of_acceptance_stops_before_dated(self):
        lines = [
            (1, "Ref: Letter of Acceptance No. NSPPL/FY2025-26/RFP/Incident Management/LoA dated 05.06.2025 for"),
            (1, "Request for Proposal issued by NHIT Southern Projects Private Limited"),
        ]
        field = _loa(lines)
        self.assertEqual(field.value, "NSPPL/FY2025-26/RFP/Incident Management/LoA")

    def test_loa_stops_before_for_the_work(self):
        lines = [
            (1, "LOA No. NSPPL/TMS/2025-26/17 for the work Design, Supply, Installation of TMS"),
        ]
        self.assertEqual(_loa(lines).value, "NSPPL/TMS/2025-26/17")

    def test_po_is_extracted_separately_and_stops_before_dtd(self):
        lines = [(1, "PO Number: NSPPL/PO/2025/44 dtd 12.08.2025")]
        self.assertIsNone(_loa(lines).value)
        self.assertEqual(_po(lines).value, "NSPPL/PO/2025/44")

    def test_tender_number_is_last_resort(self):
        lines = [(1, "Tender No: NHIT/2025/TMS/09 dated 01.04.2025")]
        field = _loa(lines)
        self.assertEqual(field.value, "NHIT/2025/TMS/09")
        self.assertIn("from_tender_no", field.notes)


class ContractorTests(unittest.TestCase):
    def test_deed_clause(self):
        doc = _doc([
            "7 (A) Example Engineering Solutions, B2/G002 Some Colony,",
            "8 SECTOR 16C, SOME CITY",
            "9. STATE (hereinafter called the \u201cContractor\u201d) and the Company,",
        ])
        masters = Masters.load()
        result = extract(doc, masters)
        self.assertEqual(result.value("Name of Contractor"), "Example Engineering Solutions")
        self.assertIn("from_deed_clause", result.fields["Name of Contractor"].notes)

    def test_no_contractor_is_blank(self):
        doc = _doc(["A document with no labels at all."])
        result = extract(doc, Masters.load())
        field = result.fields["Name of Contractor"]
        self.assertIsNone(field.value)
        self.assertIn("contractor_not_found", field.flags)

    def test_sfms_column_header_never_becomes_a_name(self):
        doc = _doc([
            ":7033: Name of applicant and his details",
            "and any request including claim shall be sent to the bank",
        ])
        result = extract(doc, Masters.load())
        field = result.fields["Name of Contractor"]
        self.assertIsNone(field.value)
        self.assertIn("contractor_not_found", field.flags)

    def test_applicant_label_with_angle_bracket(self):
        doc = _doc(["APPLICANT > K L ENTERPRISES"])
        result = extract(doc, Masters.load())
        self.assertEqual(result.value("Name of Contractor"), "K L ENTERPRISES")

    def test_deed_clause_survives_address_on_the_same_line(self):
        doc = _doc([
            "9 (A) Arya Omnitalk Wireless Solutions Pvt. Ltd..2ND FLOOR,UNITS 202,MAGARPATTA CITY",
            "10 SUMMER COURT HADAPSAR (hereinafter called the \u201cContractor\u201d) and NHIT",
        ])
        result = extract(doc, Masters.load())
        self.assertEqual(
            result.value("Name of Contractor"),
            "Arya Omnitalk Wireless Solutions Pvt. Ltd",
        )

    def test_on_behalf_of_name_wraps_to_the_next_line(self):
        doc = _doc([
            "Please find enclosed Bank Guarantee favoring yourself on behalf of: ARYA OMNITALK",
            "WIRELESS SOLUTIONS PVT LTD, 2ND FLOOR, PUNE",
        ])
        result = extract(doc, Masters.load())
        self.assertEqual(
            result.value("Name of Contractor"),
            "ARYA OMNITALK WIRELESS SOLUTIONS PVT LTD",
        )


class DateTripletTests(unittest.TestCase):
    def test_cover_letter_row(self):
        doc = _doc([
            "Ref: 0816NDLG00007526",
            "15-11-2025 06-04-2026 06-04-2026 INR 46,99,752.00",
        ])
        result = extract(doc, Masters.load(), today=dt.date(2026, 10, 28))
        self.assertEqual(result.value("From"), dt.date(2025, 11, 15))
        self.assertEqual(result.value("To"), dt.date(2026, 4, 6))
        self.assertEqual(result.value("Claim Period"), dt.date(2026, 4, 6))
        self.assertIn("from_table_row", result.fields["To"].notes)
        self.assertEqual(result.value("Status"), "Expired")


class NatureTests(unittest.TestCase):
    """The hint table matches phrases, so margin line numbers must not split them."""

    masters = Masters(
        natures=[
            "Incident Management", "Repair & rehabilititation",
            "General Management Consultancy Service", "Routine Maintenance",
            "Supply & Installation of Overhead Gantry", "TMS", "Operation",
        ],
        statuses=["Active"],
        project_options=[],
        toll_plazas=[],
    )

    def test_margin_line_number_does_not_break_the_phrase(self):
        doc = _doc([
            "\u201cWO No NSSPL/FY25-26/Bridae Repair &",
            "11 Rehabilitation \u2014 Bareilly \u2014 Sitapur\u201d for the project",
        ])
        field = extract(doc, self.masters).fields["Nature of Service"]
        self.assertEqual(field.value, "Repair & rehabilititation")
        self.assertEqual(field.flags, [])

    def test_unknown_nature_is_blank(self):
        doc = _doc(["A guarantee for something else entirely."])
        field = extract(doc, self.masters).fields["Nature of Service"]
        self.assertIsNone(field.value)
        self.assertIn("nature_not_found", field.flags)

    def test_consultancy_beats_generic_operation(self):
        doc = _doc([
            "BANK GUARANTEE",
            "General Management Consultancy Services for Operation and Management",
        ])
        field = extract(doc, self.masters).fields["Nature of Service"]
        self.assertEqual(field.value, "General Management Consultancy Service")

    def test_toll_management_system_beats_operation(self):
        doc = _doc(["BANK GUARANTEE", "Toll Management System at fee plazas"])
        field = extract(doc, self.masters).fields["Nature of Service"]
        self.assertEqual(field.value, "TMS")

    def test_routine_maintenance(self):
        doc = _doc(["BANK GUARANTEE", "Routine Maintenance at Bareilly Sitapur section"])
        field = extract(doc, self.masters).fields["Nature of Service"]
        self.assertEqual(field.value, "Routine Maintenance")

    def test_overhead_gantry(self):
        doc = _doc([
            "BANK GUARANTEE",
            "Supply, Installation, Testing and Commissioning of Overhead Gantry",
        ])
        field = extract(doc, self.masters).fields["Nature of Service"]
        self.assertEqual(field.value, "Supply & Installation of Overhead Gantry")


class BankNameTests(unittest.TestCase):
    """The bank master starts empty; OCR keyword still resolves the name."""

    def test_bank_is_read_without_a_master_list(self):
        doc = _doc(["BANK GUARANTEE", "Issued by ICICI Bank Limited, Noida"])
        masters = Masters(statuses=["Active"])
        self.assertEqual(masters.banks, [])
        result = extract(doc, masters)
        self.assertEqual(result.value("Bank Name"), "ICICI Bank Limited")
        self.assertFalse(result.fields["Bank Name"].flags)

    def test_unknown_bank_is_blank(self):
        doc = _doc(["BANK GUARANTEE", "Issued by State Bank of India"])
        field = extract(doc, Masters(statuses=["Active"])).fields["Bank Name"]
        self.assertIsNone(field.value)
        self.assertIn("bank_not_matched", field.flags)


class ProjectAndPlazaTests(unittest.TestCase):
    """Project and plaza are derived from the masters, never guessed."""

    masters = Masters(
        companies=[
            "NHIT Eastern Projects Private Limited",
            "NHIT Southern Projects Private Limited",
            "NHIT Western Projects Private Limited",
        ],
        banks=["ICICI Bank Limited", "HDFC Bank"],
        natures=["Incident Management", "Repair & rehabilititation"],
        statuses=["Active", "Expired"],
        project_options=[
            {
                "code": "BS",
                "name": "Bareilly-Sitapur",
                "company": "NHIT Southern Projects Private Limited",
                "state": "UP",
            },
            {
                "code": "KRK",
                "name": "Kachugaon-Kalijhar",
                "company": "NHIT Eastern Projects Private Limited",
                "state": "Assam",
            },
            {
                "code": "ACT",
                "name": "Amravati-Chikhali-Tarsod",
                "company": "NHIT Western Projects Private Limited",
                "state": "MH",
            },
        ],
        toll_plazas=[
            {"project": "BS", "name": "Faridpur", "chainage": "157.59"},
            {"project": "BS", "name": "Maigalganj", "chainage": "157.59"},
            {"project": "KRK", "name": "Dhalapara", "chainage": "51.5"},
        ],
    )

    def test_project_from_loa_name(self):
        doc = _doc([
            "PERFORMANCE BANK GUARANTEE",
            "LOA No. NSSPL/FY25-26/Bridge Repair & Rehabilitation - Bareilly - Sitapur",
        ])
        field = extract(doc, self.masters).fields["Project Name"]
        self.assertEqual(field.value, "BS")
        self.assertIn("derived_from_text", field.notes)

    def test_plaza_sets_project(self):
        doc = _doc(["BANK GUARANTEE", "Toll Plaza: Faridpur"])
        result = extract(doc, self.masters)
        self.assertEqual(result.value("Toll Plaza Name"), "Faridpur")
        self.assertEqual(result.value("Project Name"), "BS")
        self.assertIn("derived_from_plaza", result.fields["Project Name"].notes)

    def test_first_plaza_wins_and_other_is_noted(self):
        doc = _doc(["BANK GUARANTEE", "Faridpur and Maigalganj toll plazas"])
        result = extract(doc, self.masters)
        field = result.fields["Toll Plaza Name"]
        self.assertEqual(field.value, "Faridpur")
        self.assertIn("also_covers: Maigalganj", field.notes)
        self.assertEqual(result.plazas, ["Faridpur", "Maigalganj"])
        self.assertEqual(result.value("Project Name"), "BS")

    def test_first_plaza_missing_from_master_stays_blank(self):
        doc = _doc(["BANK GUARANTEE", "Nowhere Toll Plaza and Faridpur Toll Plaza"])
        result = extract(doc, self.masters)
        field = result.fields["Toll Plaza Name"]
        self.assertIsNone(field.value)
        self.assertIn("plaza_not_found", field.notes)
        self.assertEqual(result.plazas, ["Faridpur"])
        self.assertEqual(result.value("Project Name"), "BS")

    def test_plaza_wins_when_project_text_points_elsewhere(self):
        # The old resolver filtered plazas by the project guess first, so this
        # document came back with a blank plaza. Match the plaza in both
        # directions: Faridpur is in BS, not KRK.
        doc = _doc([
            "BANK GUARANTEE",
            "Project: KRK",
            "Toll Plaza: Faridpur",
        ])
        result = extract(doc, self.masters)
        self.assertEqual(result.value("Toll Plaza Name"), "Faridpur")
        self.assertEqual(result.value("Project Name"), "BS")
        self.assertTrue(
            any(note.startswith("project_conflict:") for note in result.fields["Project Name"].notes)
        )

    def test_project_comes_from_any_matching_plaza(self):
        doc = _doc([
            "BANK GUARANTEE",
            "Work at Nowhere Toll Plaza and Faridpur Toll Plaza",
        ])
        result = extract(doc, self.masters)
        self.assertEqual(result.value("Project Name"), "BS")
        self.assertIsNone(result.value("Toll Plaza Name"))
        self.assertEqual(result.plazas, ["Faridpur"])

    def test_plazas_spanning_projects_leave_project_ambiguous(self):
        doc = _doc(["BANK GUARANTEE", "Faridpur Toll Plaza and Dhalapara Toll Plaza"])
        result = extract(doc, self.masters)
        self.assertEqual(result.value("Toll Plaza Name"), "Faridpur")
        self.assertIsNone(result.value("Project Name"))
        self.assertIn("candidates:", result.fields["Project Name"].notes[0])

    def test_project_name_tolerates_ocr_typo_and_separators(self):
        doc = _doc(["BANK GUARANTEE", "Routine maintenance at Bareily to Sitapur section"])
        self.assertEqual(extract(doc, self.masters).value("Project Name"), "BS")

    def test_corridor_name_is_not_a_plaza(self):
        doc = _doc([
            "BANK GUARANTEE",
            "Routine Maintenance at Bareilly Sitapur Section (Km 342.000 to Km 419.520)",
        ])
        result = extract(doc, self.masters)
        self.assertEqual(result.value("Project Name"), "BS")
        self.assertIsNone(result.value("Toll Plaza Name"))
        self.assertEqual(result.plazas, [])

    def test_code_only_in_reference_line(self):
        doc = _doc(["BANK GUARANTEE", "LOA: NHIT/EPC/KRK/2025/17"])
        self.assertEqual(extract(doc, self.masters).value("Project Name"), "KRK")

    def test_short_code_in_prose_is_ignored(self):
        doc = _doc(["BANK GUARANTEE", "AS per LOA No. NSSPL/2025/17", "The corridor is not named here"])
        self.assertIsNone(extract(doc, self.masters).value("Project Name"))

    def test_no_project_is_blank(self):
        doc = _doc(["BANK GUARANTEE", "Incident Management at head office"])
        field = extract(doc, self.masters).fields["Project Name"]
        self.assertIsNone(field.value)
        self.assertIn("project_not_found", field.notes)


class CompanyNameTests(unittest.TestCase):
    """Company Name always follows the resolved project, never the OCR alias."""

    masters = ProjectAndPlazaTests.masters

    def test_project_sets_full_legal_company(self):
        doc = _doc(["BANK GUARANTEE", "Toll Plaza: Faridpur", "Beneficiary: NSPPL"])
        field = extract(doc, self.masters).fields["Company Name"]
        self.assertEqual(field.value, "NHIT Southern Projects Private Limited")
        self.assertIn("derived_from_project", field.notes)
        self.assertFalse(any(n.startswith("company_conflict") for n in field.notes))

    def test_eastern_project_sets_eastern_company(self):
        doc = _doc([
            "BANK GUARANTEE",
            "LOA: NHIT/EPC/KRK/2025/17",
            "Beneficiary: NHIT Eastern Projects Private Limited",
        ])
        self.assertEqual(
            extract(doc, self.masters).value("Company Name"),
            "NHIT Eastern Projects Private Limited",
        )

    def test_alias_is_used_when_project_is_unknown(self):
        doc = _doc(["BANK GUARANTEE", "Beneficiary: NHIT Southern Projects (NSPPL)"])
        field = extract(doc, self.masters).fields["Company Name"]
        self.assertEqual(field.value, "NHIT Southern Projects Private Limited")
        self.assertIn("derived_from_beneficiary", field.notes)

    def test_all_three_short_codes_are_aliases(self):
        cases = {
            "NEPPL": "NHIT Eastern Projects Private Limited",
            "NSPPL": "NHIT Southern Projects Private Limited",
            "NWPPL": "NHIT Western Projects Private Limited",
        }
        for code, company in cases.items():
            with self.subTest(code=code):
                doc = _doc(["BANK GUARANTEE", f"Beneficiary: {code}"])
                self.assertEqual(extract(doc, self.masters).value("Company Name"), company)

    def test_project_wins_when_beneficiary_disagrees(self):
        doc = _doc([
            "BANK GUARANTEE",
            "Toll Plaza: Faridpur",
            "Beneficiary: NHIT Eastern Projects Private Limited",
        ])
        field = extract(doc, self.masters).fields["Company Name"]
        self.assertEqual(field.value, "NHIT Southern Projects Private Limited")
        self.assertTrue(any(n.startswith("company_conflict:") for n in field.notes))

    def test_nothing_matched_is_blank(self):
        doc = _doc(["BANK GUARANTEE", "Incident Management at head office"])
        field = extract(doc, self.masters).fields["Company Name"]
        self.assertIsNone(field.value)
        self.assertIn("company_not_found", field.flags)


class PipelineExtractionTests(unittest.TestCase):
    """Datalab supplies the values; local code only normalises and resolves."""

    masters = ProjectAndPlazaTests.masters
    today = dt.date(2025, 1, 1)

    data = {
        "company_name": "NHIT Southern Projects Private Limited",
        "project_name": "Bareilly-Sitapur",
        "toll_plaza_names": ["Faridpur", "Maigalganj"],
        "bg_type": "PBG",
        "bg_type_wording": "Performance Security",
        "nature_of_service": "Incident Management",
        "contractor_name": "Example Infra Limited",
        "bg_no": "1234AB12345678901",
        "bg_amount": 4078080,
        "bg_amount_in_words": (
            "Rupees Forty Lakh Seventy Eight Thousand Eighty Only"
        ),
        "from_date": "15-11-2025",
        "to_date": "06-04-2026",
        "claim_expiry_date": "06-04-2026",
        "bank_name": "HDFC Bank",
        "loa_or_tender_number": "LOA/WEB/001",
        "po_number": None,
        "authorized_signatories": [
            {"name": "A. Signatory", "sol_id": "880919", "designation": "DM-II"}
        ],
        "stamp_duty_certificates": [
            {
                "certificate_no": "IN-UP98274247513780Y",
                "certificate_issued_date": "28-Jul-2026 03:35 PM",
                "description_of_document": "Article 12 (A) Bank Guarantee",
                "consideration_price": None,
                "stamp_duty_amount": 2600,
            }
        ],
    }

    def test_fields_are_mapped_from_the_pipeline_json(self):
        result = extract_from_pipeline(
            _doc(["ignored"]), self.data, self.masters, today=self.today
        )

        self.assertEqual(
            result.value("Company Name"), "NHIT Southern Projects Private Limited"
        )
        self.assertEqual(result.value("Project Name"), "BS")
        self.assertEqual(result.value("Toll Plaza Name"), "Faridpur")
        self.assertIn("also_covers: Maigalganj", result.fields["Toll Plaza Name"].notes)
        self.assertEqual(result.value("Types of BG"), "PBG")
        self.assertEqual(result.value("Nature of Service"), "Incident Management")
        self.assertEqual(result.value("Name of Contractor"), "Example Infra Limited")
        self.assertEqual(result.value("BG No"), "1234AB12345678901")
        self.assertEqual(result.value("BG Amount"), 4078080.0)
        self.assertEqual(result.value("From"), dt.date(2025, 11, 15))
        self.assertEqual(result.value("To"), dt.date(2026, 4, 6))
        self.assertEqual(result.value("Claim Period"), dt.date(2026, 4, 6))
        self.assertEqual(result.value("Bank Name"), "HDFC Bank")
        self.assertEqual(result.value("LOA Number"), "LOA/WEB/001")
        self.assertIsNone(result.value("PO Number"))
        self.assertIn("po_not_found", result.fields["PO Number"].flags)
        self.assertEqual(result.value("Status"), "Active")

    def test_extras_are_kept_for_the_archive_and_review_card(self):
        result = extract_from_pipeline(
            _doc(["ignored"]), self.data, self.masters, today=self.today
        )

        self.assertEqual(
            result.extras["authorized_signatories"][0]["name"], "A. Signatory"
        )
        stamp = result.extras["stamp_duty_certificates"][0]
        self.assertEqual(stamp["stamp_duty_amount"], 2600)
        self.assertNotIn("consideration_price", stamp)
        self.assertEqual(
            result.extras["bg_amount_in_words"],
            "Rupees Forty Lakh Seventy Eight Thousand Eighty Only",
        )
        self.assertEqual(result.extras["bg_type_wording"], "Performance Security")
        report = result.report()
        self.assertEqual(report["extras"]["authorized_signatories"][0]["sol_id"], "880919")

    def test_missing_values_are_blank_with_flags(self):
        result = extract_from_pipeline(
            _doc(["ignored"]), {}, self.masters, today=self.today
        )

        for column, flag in {
            "Company Name": "company_not_found",
            "Types of BG": "bg_type_not_found",
            "Nature of Service": "nature_not_found",
            "Name of Contractor": "contractor_not_found",
            "BG No": "bg_no_not_found",
            "BG Amount": "amount_not_found",
            "From": "from_not_found",
            "To": "to_not_found",
            "Claim Period": "claim_not_found",
            "Bank Name": "bank_not_matched",
            "LOA Number": "loa_not_found",
            "PO Number": "po_not_found",
        }.items():
            with self.subTest(column=column):
                field = result.fields[column]
                self.assertIsNone(field.value)
                self.assertIn(flag, field.flags)

    def test_amount_and_words_disagree_is_flagged(self):
        data = dict(self.data, bg_amount=1000)
        field = extract_from_pipeline(
            _doc(["ignored"]), data, self.masters, today=self.today
        ).fields["BG Amount"]
        self.assertEqual(field.value, 1000.0)
        self.assertIn("amount_vs_words_mismatch", field.flags)

    def test_unparsed_date_is_blank_and_noted(self):
        data = dict(self.data, from_date="sometime in 2025")
        field = extract_from_pipeline(
            _doc(["ignored"]), data, self.masters, today=self.today
        ).fields["From"]
        self.assertIsNone(field.value)
        self.assertIn("from_not_found", field.flags)
        self.assertTrue(any(note.startswith("unparsed_date:") for note in field.notes))

    def test_bg_number_differs_from_filename_is_flagged(self):
        data = dict(self.data, bg_no="9999ZZ99999999999")
        field = extract_from_pipeline(
            _doc(["ignored"]), data, self.masters, today=self.today
        ).fields["BG No"]
        self.assertEqual(field.value, "9999ZZ99999999999")
        self.assertIn("bg_no_differs_from_filename", field.flags)

    def test_plaza_sets_the_project_when_no_project_is_printed(self):
        data = dict(self.data, project_name="")
        result = extract_from_pipeline(
            _doc(["ignored"]), data, self.masters, today=self.today
        )
        self.assertEqual(result.value("Project Name"), "BS")
        self.assertIn("derived_from_plaza", result.fields["Project Name"].notes)

    def test_unknown_project_is_blank_and_not_guessed(self):
        data = dict(
            self.data,
            project_name="Some Other Corridor",
            toll_plaza_names=["Nowhere Plaza"],
        )
        result = extract_from_pipeline(
            _doc(["ignored"]), data, self.masters, today=self.today
        )
        self.assertIsNone(result.value("Project Name"))
        self.assertIsNone(result.value("Toll Plaza Name"))
        self.assertIn("plaza_not_found", result.fields["Toll Plaza Name"].notes)


if __name__ == "__main__":
    unittest.main()
