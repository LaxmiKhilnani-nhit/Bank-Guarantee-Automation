---
id: PLAN-04
title: OCR and Field Extraction
status: active
last_updated: 2026-10-08
version: 3
inputs: ["Sample_Bank_Guarantees/", "BG_OCR_Options_Comparison.md"]
outputs: ["Structured BG fields + confidence scores", "DI JSON archive"]
related: ["PLAN-02", "PLAN-05"]
---

# 04 — OCR and Field Extraction

> **Implementation status (2026-11-02).** OCR runs locally with Indra as the
> default and Tesseract available for fallback and comparison. Azure Document
> Intelligence is now an optional test engine and runs only when selected;
> those selected PDFs are sent to the configured Central India endpoint.
> Indra's `auto` mode uses suitable PDF text and OCRs scanned pages; `ocr`
> mode reads each page as an image. See `backend/README.md` for setup and
> credential handling. The field rules and blank-beats-wrong principle still
> apply.

## 1. Purpose

This document explains how a scanned BG PDF becomes structured data. Read it
before building the extraction service or tuning accuracy.

## 2. Strategy

The application supports three OCR paths: local Indra, local Tesseract, and
optional Azure Document Intelligence (DI) in Central India. Indra remains the
default. Azure is currently intended for explicit testing and comparison; the
user selects it before upload, which sends that PDF to the configured service.

1. **Configure for testing.** Set the endpoint and key in the ignored local
   `.env` file. The app uses `prebuilt-layout` to get lines and word confidence.
2. **Compare.** Use sample PDFs with the local Indra and Tesseract results;
   review page coverage, field disagreements, and confidence together.
3. **Approve future use.** Confirm Azure resource pricing, retention, and
   deployment policy before routine processing. If accuracy on Project, LOA, or Claim fields is
   below 95% across 20+ samples, train a custom extraction model on 15–25
   labelled BGs from different banks.
4. **Archive.** Keep the OCR text, per-engine comparison, and extraction report
   in the local run archive for review.

Expected volume: 20–25 PDFs/month, 6–10 pages each, about 200 pages/month.
Scanned printed English. Estimated cost: Layout ~$2/month, Custom ~$6/month.

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

- Auto-fill a field only when DI confidence is **0.75 or higher**.
  Otherwise leave it blank and flag it for review.
- Never lower the threshold to force a fill. Skewed scans and stamps go to
  humans.
- Measure per-field precision on BG No, Amount, To, Claim, and LOA over 20+
  samples including `Sample_Bank_Guarantees/`.

## 5. Acceptance criteria

- [ ] Every extraction stores raw DI JSON for audit.
- [ ] Fields below 0.75 confidence appear blank and flagged, not guessed.
- [ ] Accuracy report exists before training a custom model.

## References

- Comparison doc: `../BG_OCR_Options_Comparison.md`
- Data model: `02-data-model.md`
