---
id: PLAN-04
title: OCR and Field Extraction
status: active
last_updated: 2026-11-04
version: 4
inputs: ["Sample_Bank_Guarantees/", "BG_OCR_Options_Comparison.md"]
outputs: ["Structured BG fields + confidence scores", "Datalab page-text archive"]
related: ["PLAN-02", "PLAN-05"]
---

# 04 — OCR and Field Extraction

> **Implementation status (2026-11-04).** OCR runs through the Datalab API with
> one fixed mode (`fast` by default, override with `BG_DATALAB_MODE`). Every
> uploaded PDF is sent to Datalab; the register and review archive stay local.
> The Tesseract, Indra and Azure adapters have been removed. `bgauto/compare.py`
> is kept for reference, but the upload flow no longer runs or displays an
> engine comparison. See `backend/README.md` for setup and credential handling.
> The field rules and blank-beats-wrong principle still apply.

## 1. Purpose

This document explains how a scanned BG PDF becomes structured data. Read it
before building the extraction service or tuning accuracy.

## 2. Strategy

The application uses one OCR path: the Datalab API. Datalab converts the PDF to
a page-aware JSON tree; the adapter turns that into the page text the extractor
uses. There is no local OCR engine and no engine selection in the UI.

1. **Configure.** Add `DATALAB_API_KEY` to the ignored local `.env` file (or run
   `datalab login` once). The key is never written to source or logs.
2. **Convert.** Each upload or CLI file is submitted with `output_format=json`
   and the configured mode. Image extraction and captions are disabled.
3. **Archive.** Keep the resulting page text, confidence source, and extraction
   report in the local run archive for review.
4. **Approve future use.** Confirm Datalab pricing, data retention, and
   deployment policy before routine processing. If accuracy on Project, LOA, or
   Claim fields is below 95% across 20+ samples, tune the field rules before
   considering any custom model.

Expected volume: 20–25 PDFs/month, 6–10 pages each, about 200 pages/month.
Scanned printed English. Datalab bills per page; `fast` keeps the cost down and
`accurate` is available per CLI run when a document needs it.

## 3. Field rules

| Field | Where to look | Rule | If unsure |
|-------|--------------|------|-----------|
| BG No | Near "BG No" label | Regex for the label plus bank patterns (`ILG`, `GT`, `BG`), 6–40 chars | Block Confirm, send to review |
| Bank Name | Letterhead or stamp | Keyword match, then normalise (SBI, HDFC, ICICI, Axis, PNB…) | Leave for reviewer dropdown |
| BG Amount | Near "amount" | Match `INR`/`Rs` + number, strip commas, store numeric | Block Confirm if missing |
| From | Near "effective", "issue" | Date `DD-MM-YYYY` closest to the label | Block Confirm if missing |
| To | Near "expiry", "valid till" | Date `DD-MM-YYYY`; this is the T-7 anchor | Block Confirm if missing |
| Claim Period | Near "claim" | Date or text as printed | May stay blank (ST008) |
| LOA Number | Near "LOA" or "Letter of Acceptance" | Label regex | May stay blank |
| PO Number | Near "Purchase Order", "P.O.", or "Work Order" | Label regex or manual entry | May stay blank |
| Contractor | "Applicant" or "on behalf" block | Take the block as-is | Editable text |
| Beneficiary | "In favour of" block | Info only; not a tracker column | Info only |
| Company, Project, Toll Plaza, Types, Nature, Status, Extended | — | Dropdowns, never OCR-trusted | Reviewer must pick |

Full regex patterns live in the implementation. The principle matters more
than the pattern: prefer blank over wrong.

## 4. Confidence policy

- Datalab returns one `parse_quality_score` per document (0–5) when it has one.
  The app shows `score / 5` as the page confidence with source
  `datalab_quality`; it is a document-level quality signal, not a per-word
  probability. `fast` often omits it, in which case no confidence is shown.
- Field values are filled by the label-anchored rules, not by a confidence
  threshold alone. A missing or contradictory value stays blank and flagged.
- Never lower a validation rule to force a fill. Skewed scans and stamps go to
  humans.
- Measure per-field precision on BG No, Amount, To, Claim, and LOA over 20+
  samples including `Sample_Bank_Guarantees/`.

## 5. Acceptance criteria

- [ ] Every extraction stores the Datalab page text, confidence source and extraction report for audit.
- [ ] Fields that fail a rule appear blank and flagged, not guessed.
- [ ] Accuracy report exists before tuning or replacing the field rules.

## References

- Comparison doc: `../BG_OCR_Options_Comparison.md`
- Data model: `02-data-model.md`
