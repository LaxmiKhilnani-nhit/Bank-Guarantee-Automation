# Fields To Capture From BG Through Datalab OCR Pipeline

Company Name
Project Name
Toll Plaza Name
Types of BG
Nature of Service
Name of Contrator
BG No
BG Amount
BG Amount in Words
From
To
Claim Period
Extended	
Bank Name
Status
Remarks 	
LOA / Tender No.
PO No.
Authorized Signatories
- signatory name
- signatory identifier / SOL ID
Stamp Duty
- Certificate NO.
- Certificate Issued Date
- Account Reference
- Unique Doc. Reference
- Description Of Document
- Consideration Price (Rs.)  1
- First Party
- Second Party
- Stamp Duty Paid By
- Stamp Duty Amount(Rs.)
- Stamp Serial Number

---

## Coverage against schema v1 (2026-11-10)

This wish-list predates the published schema. The production schema is **v1**
(18 fields), published in Datalab as pipeline `BG_Fields_Extraction_dev`
(`pl_INVNggnY7MaE` on the development account). Production runs on a fresh
Datalab account, so the same schema publishes there as **version 1**; the old
`v3` label was only an internal working number. It does the following:

| Wanted | Schema v1 | Notes |
|---|---|---|
| Company Name | `company_name` | Resolved to the company master; printed value kept when unmatched |
| Project Name | `project_name` | Matched to the project master |
| Toll Plaza Name | `toll_plaza_names` (array) | First matched plaza becomes the column; the rest go to `also_covers` and extra Excel rows |
| Types of BG | `bg_type` | `PBG` / `ABG`; printed wording kept in extras as `bg_type_wording` |
| Nature of Service | `nature_of_service` | Fuzzy-matched to the nature master |
| Name of Contractor | `contractor_name` | Cleaned and matched to the contractor master |
| BG No | `bg_no` | Validated and cross-checked against the filename |
| BG Amount | `bg_amount` | Numeric; cross-checked against the words |
| BG Amount in Words | `bg_amount_in_words` | Extras; also the amount cross-check |
| From | `from_date` | `DD-MM-YYYY` |
| To | `to_date` | `DD-MM-YYYY`; T-7 anchor |
| Claim Period | `claim_expiry_date` | `DD-MM-YYYY` |
| Extended | *not extracted* | Reviewer dropdown (by design) |
| Bank Name | `bank_name` | Matched to the bank master/aliases |
| Status | *not extracted* | Computed from `to_date` (Expired/Active) |
| Remarks | *not extracted* | Reviewer free text |
| LOA / Tender No. | `loa_or_tender_number` | |
| PO No. | `po_number` | |
| Authorized Signatories | `authorized_signatories` (array) | Extras only |
| - signatory name | `name` | |
| - signatory identifier / SOL ID | `sol_id` | `designation` was also added to the schema |
| Stamp Duty | `stamp_duty_certificates` (array) | Extras only |
| - Certificate NO. | `certificate_no` | |
| - Certificate Issued Date | `certificate_issued_date` | |
| - Account Reference | `account_reference` | |
| - Unique Doc. Reference | `unique_doc_reference` | |
| - Description Of Document | `description_of_document` | |
| - Consideration Price (Rs.) | `consideration_price` | |
| - First Party | `first_party` | |
| - Second Party | `second_party` | |
| - Stamp Duty Paid By | `stamp_duty_paid_by` | |
| - Stamp Duty Amount(Rs.) | `stamp_duty_amount` | |
| - Stamp Serial Number | `stamp_serial_number` | |

### Still wanted, not in v1

- **`Extended` / `Status` / `Remarks`** are deliberately not extracted: `Status`
  is computed from `To`, and `Extended`/`Remarks` are reviewer-owned.
- **`currency`** was listed in early docs but is not a v1 field. The app will
  surface it only if a later schema version adds the key.
- **`project_code`** is read defensively by the mapper as a project fallback but
  is not a v1 field; v1 supplies `project_name` only.

Everything else on the wish-list is covered by v1. If a new field is needed,
add it to the schema in the Datalab UI, publish a new version, bump
`BG_DATALAB_PIPELINE_VERSION`, and update this table, `plan/04-ocr-extraction.md`
§3.1 and the JSON snapshots in `docs/`.