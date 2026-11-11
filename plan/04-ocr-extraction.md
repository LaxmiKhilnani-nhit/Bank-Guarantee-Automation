---
id: PLAN-04
title: OCR and Field Extraction
status: active
last_updated: 2026-11-10
version: 9
inputs: ["Sample_Bank_Guarantees/", "BG_OCR_Options_Comparison.md"]
outputs: ["Structured BG fields + confidence scores", "Datalab page-text archive", "docs/datalab-schema-v1.json", "docs/datalab-pipeline-v1.json"]
related: ["PLAN-02", "PLAN-05"]
---

# 04 — OCR and Field Extraction

> **Implementation status (2026-11-10).** Extraction runs through a published
> Datalab pipeline (`pl_INVNggnY7MaE` on the development account, active version
> 3; production deploys the same schema on a fresh account as version 1):
> `convert` then structured `extract` in `turbo` mode. The extract step's JSON is
> authoritative; the app normalises it and resolves masters, with no OCR-text
> rule fallback.
> Every uploaded PDF is sent to Datalab; the register and run archive stay
> local. `bgauto/compare.py` is kept for reference, but the upload flow no
> longer runs or displays an engine comparison. See `backend/README.md` for
> setup and credential handling. The blank-beats-wrong principle still applies.

## 1. Purpose

This document explains how a scanned BG PDF becomes structured data. Read it
before changing the pipeline schema or the mapping code.

## 2. Strategy

The application uses one extraction path: a Datalab pipeline. Datalab converts
the PDF to page text and then extracts the tracker fields from that text using
the schema stored in the pipeline. There is no local OCR engine, no engine
selection in the UI, and no local text-rule fallback.

1. **Configure.** Add `DATALAB_API_KEY` to the ignored local `.env` file (or run
   `datalab login` once). Pin the pipeline release with
   `BG_DATALAB_PIPELINE_ID` and `BG_DATALAB_PIPELINE_VERSION`. The key is never
   written to source or logs.
2. **Run.** Each upload or CLI file starts the pipeline with `skip_cache=true`
   (a cached convert checkpoint has no downloadable result) and follows both
   steps: `converting` then `extracting`.
3. **Map.** `convert` supplies page text for the archive and the optional
   official-name lookup. `extract` supplies the field JSON; local code only
   normalises dates/amounts/references and resolves company, project, plaza,
   nature, contractor and bank against the masters. A missing or invalid value
   becomes a blank cell with a flag.
4. **Archive.** Write both raw step results and the normalised report under
   `out/web/<run>/pipeline/` and `out/web/<run>/ocr/` immediately; Datalab
   expires results after about a day.
5. **Approve future use.** Confirm Datalab pricing, data retention, and
   deployment policy before routine processing.

Expected volume: 20–25 PDFs/month, 6–10 pages each, about 200 pages/month.
Scanned printed English. Datalab bills per page; the pipeline's convert and
extract settings (including `turbo`) control cost.

## 3. Field sources

| Field | Source | If unsure |
|-------|--------|-----------|
| BG No | Datalab `bg_no`, validated and cross-checked against the filename | Blank + flag |
| Bank Name | Datalab `bank_name`, matched to the bank master | Blank + flag |
| BG Amount | Datalab `bg_amount`, cross-checked against `bg_amount_in_words` | Blank + flag |
| From / To / Claim Period | Datalab dates, normalised to `YYYY-MM-DD` | Blank + flag |
| LOA Number / PO Number | Datalab references | Blank + flag |
| Contractor | Datalab `contractor_name`, cleaned and matched | Printed value when unmatched |
| Company / Project / Toll Plaza / Types / Nature | Datalab values resolved against the masters | Blank + note for the reviewer |
| Status / Extended / Remarks | Computed or reviewer-owned | Reviewer or blank |
| Extras | Authorised signatories, stamp-duty certificates, amount in words, type wording, printed project name | Archive + review card only; not columns |

### 3.1 Schema v1 field contract (reference)

The production schema is **v1** (18 fields). Datalab publishes it as pipeline
**`BG_Fields_Extraction_dev`** (`pl_INVNggnY7MaE`); the production deployment
uses a fresh account, so it will be **version 1**. Extract mode is `turbo` (the
old `v3` label was only an internal working number). The table below mirrors the
18 fields so a reader of this repository knows what the app expects. It is a
reference only: the schema itself lives in the Datalab UI and is not executed or
synced from here. When the UI schema changes, publish a new version, bump
`BG_DATALAB_PIPELINE_VERSION`, and update this table in the same change.

| # | Datalab key | Type | Used as |
|---|---|---|---|
| 1 | `company_name` | string | **Company Name** - beneficiary/SPV; resolved against the company master, printed value kept when unknown |
| 2 | `project_name` | string | **Project Name** - corridor as printed; matched to the project master, copy kept in extras |
| 3 | `toll_plaza_names` | array of string | **Toll Plaza Name** - first matched plaza; the rest become `also_covers` and extra workbook rows |
| 4 | `bg_type` | string | **Types of BG** - `PBG` / `ABG` |
| 5 | `bg_type_wording` | string | Fallback for **Types of BG**; kept in extras as printed |
| 6 | `nature_of_service` | string | **Nature of Service** - fuzzy-matched to the nature master |
| 7 | `contractor_name` | string | **Name of Contractor** - cleaned and matched to the contractor master |
| 8 | `bg_no` | string | **BG No** - normalised, validated, cross-checked against the filename |
| 9 | `bg_amount` | number | **BG Amount** - plain INR number, cross-checked against the words |
| 10 | `bg_amount_in_words` | string | Extras; cross-check for **BG Amount** |
| 11 | `from_date` | string `DD-MM-YYYY` | **From** |
| 12 | `to_date` | string `DD-MM-YYYY` | **To** (the T-7 anchor) |
| 13 | `claim_expiry_date` | string `DD-MM-YYYY` | **Claim Period** |
| 14 | `bank_name` | string | **Bank Name** - matched to the bank master/aliases |
| 15 | `loa_or_tender_number` | string | **LOA Number** |
| 16 | `po_number` | string | **PO Number** |
| 17 | `authorized_signatories` | array of object | Extras only: `name`, `sol_id`, `designation` |
| 18 | `stamp_duty_certificates` | array of object | Extras only: `certificate_no`, `certificate_issued_date`, `account_reference`, `unique_doc_reference`, `description_of_document`, `consideration_price`, `first_party`, `second_party`, `stamp_duty_paid_by`, `stamp_duty_amount`, `stamp_serial_number` |

What the adapter does with the raw extract payload (`pipeline.py`):

- Keeps the extracted field keys (the 18 above). Envelope keys (`success`,
  `status`, `page_count`, `cost_breakdown`, ...) and `page_schema` /
  `extraction_schema_json` wrappers are dropped after the JSON is read.
- Drops `*_citations` and `*_score` if a future mode adds them, so per-field
  confidence is never invented.
- Treats an empty string/array as "not extracted": the column stays blank with
  a flag, and the extra is omitted.
- Dates are accepted as `DD-MM-YYYY` or ISO `YYYY-MM-DD` and stored as ISO in
  the report; a value that cannot be parsed is blank + flag.
- `currency` and `project_code` are **not** in schema v1. `extract.py` reads
  them defensively and uses them only if a later schema version adds the keys;
  no v1 document produces them.

The schema itself lives in the Datalab UI, not in this repository. Change it
there, publish a new version, then bump `BG_DATALAB_PIPELINE_VERSION` and update
the table above.

**Local snapshots (survive account/provider changes).** The exact v1 schema and
the full pipeline definition are saved in the repository:

- `docs/datalab-schema-v1.json` - the `page_schema` object, pretty-printed and
  paste-back ready (18 fields, `ExtractionSchema`).
- `docs/datalab-pipeline-v1.json` - schema label, pipeline name/id, the source
  account's active version (3), the production target version (1), extraction
  mode, fetch date, and both step definitions (convert settings and extract
  settings including the schema string).
- `docs/Fields To Capture From BG Through D.md` - the original field wish-list,
  followed by a coverage table showing what schema v1 covers and what is
  intentionally left to the reviewer (`Extended`, `Status`, `Remarks`).

These files are reference artifacts only; the app never reads them and there is
no sync script. They were fetched from the development pipeline
`pl_INVNggnY7MaE` on 2026-11-10, when that account's active version was 3. The
production pipeline is deployed on a fresh Datalab account, so it will publish
the same schema as **version 1**; the snapshot records both numbers
(`source_active_version: 3`, `production_version: 1`). On the development
account, set `BG_DATALAB_PIPELINE_VERSION=3` (or leave it empty to follow the
active version) until the production account is live. To restore after a
Datalab account rotation or a provider change: create a pipeline in the new
account, paste `datalab-schema-v1.json` into the extract step, copy the convert
settings from `datalab-pipeline-v1.json`, publish, then set
`BG_DATALAB_PIPELINE_ID` to the new pipeline id and
`BG_DATALAB_PIPELINE_VERSION=1` (or leave it empty to follow the active
version). Regenerate both snapshots and update this section whenever the
version is bumped.

## 4. Confidence policy

- Datalab returns one `parse_quality_score` per document (0–5) when it has one.
  The app shows `score / 5` as the page confidence with source
  `datalab_quality`; it is a document-level quality signal, not a per-word
  probability. `turbo` usually omits it, in which case no confidence is shown.
- Datalab's extracted values are authoritative. A missing or contradictory value
  stays blank and flagged; local code does not re-read the text to fill it.
- Never lower a validation rule to force a fill. Skewed scans and stamps go to
  humans.
- Measure per-field precision on BG No, Amount, To, Claim, and LOA over 20+
  samples including `Sample_Bank_Guarantees/`.

## 5. Acceptance criteria

- [x] Every extraction stores both raw Datalab step results and the normalised report for audit.
- [x] Fields that fail a check appear blank and flagged, not guessed.
- [ ] Accuracy report exists before changing the pipeline schema or the mapping code.

## References

- Comparison doc: `../BG_OCR_Options_Comparison.md`
- Data model: `02-data-model.md`
