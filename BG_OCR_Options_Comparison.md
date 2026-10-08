# BG Automation — OCR / Extraction Options Comparison

**Date:** 7 October 2026
**Use case:** Bank Guarantee (BG) PDFs uploaded on dashboard, OCR + structured details extraction, write to live SharePoint Excel, T-7 expiry email via M365 Outlook
**Volume assumption:** 20–25 PDFs/month, 6–10 pages each = ~200 pages/month, ~2,400 pages/year
**Document type:** Printed English only (no handwriting requirement)

---

## 1. Executive Summary + Recommendation

**Recommended: Azure AI Document Intelligence (Custom / Layout) deployed in Central India.**

Rationale:

- Lowest enterprise extraction cost at this volume: ~$2–$6/month (Rs 180–540/month).
- Only option that simultaneously satisfies all three hard constraints: (a) India-only data residency with a mature Central India endpoint, (b) native M365 / Entra ID / SharePoint / Outlook integration, and (c) enterprise support with no open-source components.
- Custom model training allows field-level extraction tuned to BG formats (BG number, issuing bank, beneficiary, applicant, amount, issue date, expiry date, claim period) with high accuracy on printed text.
- Operational simplicity: single Azure tenant, same Entra ID used for app login, no cross-cloud data transfer, no additional hosting fee for the OCR service itself.

Google Document AI and AWS Textract are technically capable and have Mumbai regions, but introduce cross-cloud identity, data-governance, and M365 integration overhead with no cost advantage. ABBYY Vantage is highly accurate but priced at ~$10,000+/year minimum, unjustified for 200 pages/month. AI Builder is M365-native but ~3–8x more expensive than Azure DI at this volume and less tunable for varied BG layouts.

---

## 2. Requirements Recap

| # | Requirement | Detail |
|---|-------------|--------|
| R1 | Input | BG PDFs, 20–25 files/month, 6–10 pages each, ~200 pages/month |
| R2 | Document type | Printed English only; tables and key-value fields; stamp/signature noise tolerated, no handwriting dependency |
| R3 | Functions | OCR + structured extraction (BG no., bank, dates, amount, parties), write to live SharePoint Excel, T-7 expiry email via M365 Outlook |
| R4 | Data residency | India-only, strict. Data at rest and processing must remain in India |
| R5 | Identity | Entra ID login mandatory |
| R6 | Enterprise-only | No open-source OCR / LLM (no Tesseract, EasyOCR, PaddleOCR, self-hosted LLMs) |
| R7 | Priorities | 1. Cost, 2. Accuracy, 3. M365 fit, 4. Operational simplicity |
| R8 | Hosting | Prefer reuse of existing Windows VM; App Service as alternative |

---

## 3. Options Comparison

All costs normalised to 200 pages/month.

| Criteria | Azure AI Document Intelligence | Google Cloud Document AI | AWS Textract | ABBYY Vantage | Microsoft AI Builder |
|----------|-------------------------------|--------------------------|--------------|---------------|----------------------|
| **OCR cost (list)** | Read: $1.50 / 1,000 pages | OCR: $1.50 / 1,000 pages | Detect Document Text: $1.50 / 1,000 pages | Included in platform licence, custom quote | Included in $0.08 / page (via Copilot / Power Automate credits) |
| **Extraction cost (list)** | Layout: $10 / 1,000 pages; Custom classification + extraction: $30 / 1,000 pages | Custom Extractor / Form Parser: $30 / 1,000 pages + processor hosting $0.05 / hour (~$36 / month per processor) | Tables: $15 / 1,000 pages; Forms (key-value): $50 / 1,000 pages | Included in platform licence, custom quote | Document processing: ~$0.08 / page |
| **Cost at 200 pgs/mo** | Read: $0.30/mo; Layout: $2.00/mo; Custom: $6.00/mo | OCR only: $0.30/mo; Custom + hosting: ~$42.00/mo | Detect only: $0.30/mo; Detect + Tables: ~$3.30/mo; Detect + Tables + Forms: ~$13.30/mo | ~$833+/mo amortised ($10,000+/yr minimum) | ~$16.00/mo |
| **India residency** | Yes — Central India available; configure resource in Central India | Partial — Mumbai (asia-south1) available for select processors; residency controls less straightforward for this workload | Yes — Mumbai (ap-south-1) available | No — SaaS hosted US/EU; no India data centre for standard offering | Yes — Power Platform / Dataverse environment can be provisioned in India |
| **M365 / Entra ID fit** | Native — same tenant, Entra ID auth, SDK + REST, SharePoint/Graph integration | Poor — separate Google IAM, API keys / Workload Identity Federation, custom bridge to M365 | Poor — separate AWS IAM, custom bridge to M365 | Moderate — SSO/SAML + REST API, but separate platform and licence management | Native — built into Power Platform, Entra ID by default |
| **Accuracy for printed BGs** | High — Read/Layout very strong on printed English + tables; Custom trainable on BG layouts | High — strong OCR and custom extractor quality | High — strong on printed text, tables and forms; tuning via queries is more limited | Very high — purpose-built for document skill extraction, strong on noisy scans | Good — adequate for standard forms; less tunable for varied BG bank formats |

Notes:

- Google hosting fee applies per custom processor and accrues 24x7 (~$36/month) even with zero traffic. One BG processor = ~$36/month fixed before any per-page charge.
- AWS costs are additive: a full BG extraction needing text + tables + forms is the sum of each feature applied to the same pages.
- AI Builder pricing varies by licensing route (Copilot credits, Power Automate premium, pay-as-you-go); $0.08/page is the representative consumption figure used here.
- ABBYY pricing is quote-based; $10,000+/year reflects current entry-level SaaS/licence floor for Vantage with extraction skills.

---

## 4. Cost Math — 200 Pages / Month (USD + INR, $1 = Rs 90)

| Service / Tier | Formula | USD / month | INR / month | USD / year | INR / year |
|----------------|---------|-------------|-------------|------------|------------|
| Azure DI — Read (OCR only) | 0.2 x $1.50 | $0.30 | Rs 27 | $3.60 | Rs 324 |
| Azure DI — Layout | 0.2 x $10.00 | $2.00 | Rs 180 | $24.00 | Rs 2,160 |
| Azure DI — Custom extraction | 0.2 x $30.00 | $6.00 | Rs 540 | $72.00 | Rs 6,480 |
| Google — OCR only | 0.2 x $1.50 | $0.30 | Rs 27 | $3.60 | Rs 324 |
| Google — Custom + 1 processor hosting | 0.2 x $30.00 + $36.00 | $42.00 | Rs 3,780 | $504.00 | Rs 45,360 |
| AWS — Detect only | 0.2 x $1.50 | $0.30 | Rs 27 | $3.60 | Rs 324 |
| AWS — Detect + Tables | $0.30 + 0.2 x $15.00 | $3.30 | Rs 297 | $39.60 | Rs 3,564 |
| AWS — Detect + Tables + Forms | $0.30 + $3.00 + 0.2 x $50.00 | $13.30 | Rs 1,197 | $159.60 | Rs 14,364 |
| AI Builder (~$0.08/page) | 200 x $0.08 | $16.00 | Rs 1,440 | $192.00 | Rs 17,280 |
| ABBYY Vantage (entry floor) | $10,000 / 12 (min) | ~$833.00 | ~Rs 75,000 | ~$10,000.00 | ~Rs 900,000 |

Interpretation:

- At 200 pages/month, Azure Custom ($6/month) is ~7x cheaper than Google Custom with hosting ($42/month), ~2–3x cheaper than full AWS extraction ($13.30/month), ~2.7x cheaper than AI Builder ($16/month), and ~139x cheaper than ABBYY entry pricing.
- Even Azure Custom for a full year ($72) costs less than one month of Google Custom hosting or ABBYY amortised cost.
- OCR-only tiers ($0.30/month) are not sufficient alone — BG field extraction requires Layout/Custom (Azure), Custom Extractor (Google), Tables+Forms (AWS), or equivalent.

Custom model training and labelling effort is one-time and not included above; Azure, Google, and ABBYY all require a labelled set of sample BGs (typically 10–30 samples across issuing banks).

---

## 5. Data Residency Verdict

| Option | India Residency Verdict |
|--------|-------------------------|
| Azure DI in Central India | **Compliant.** Provision Document Intelligence resource in Central India; storage, logging, and App/DB also in Central India. Single-tenant boundary, straightforward audit. |
| Google Document AI (Mumbai) | **Conditionally compliant, not recommended.** Mumbai endpoint exists for select processors but adds cross-cloud data flow, separate audit trail, and Google-side support/data-access considerations. Weakens strict residency posture. |
| AWS Textract (Mumbai) | **Conditionally compliant, not recommended.** Same concern as Google — technically processable in ap-south-1, but cross-cloud to M365 increases compliance surface. |
| ABBYY Vantage SaaS | **Non-compliant for strict India-only.** Standard SaaS regions are US/EU; India hosting only via private deployment at significantly higher cost. |
| AI Builder (India environment) | **Compliant if environment is pinned to India.** Acceptable on residency, fails on cost and tunability relative to Azure DI. |

Conclusion: only Azure DI and AI Builder satisfy strict India-only residency without architectural compromise. Azure DI is preferred on cost and accuracy tuning.

---

## 6. Hosting Options

Assumptions: app stack is dashboard (upload) + extraction worker + SharePoint Excel writer + Outlook mailer, all under Entra ID login.

### Option A — Existing Windows VM (preferred for Phase 1)

- Deploy app as a Windows Service / scheduled task + lightweight web dashboard (e.g., Flask/FastAPI behind IIS or direct port with reverse proxy).
- Azure DI called via SDK/REST; SharePoint Excel via Microsoft Graph; mail via Graph (M365 Outlook) — no SMTP credentials needed.
- Pros: zero additional hosting cost, fastest to deliver, full control, data stays in existing boundary.
- Cons: VM becomes single point of failure; manual patching, backups, and scaling; needs service hardening (auto-restart, log rotation, disk monitoring).
- Best for: pilot and first 6–12 months at 20–25 PDFs/month.

### Option B — Azure App Service (Central India)

- Web App (dashboard) + WebJob/Function (extraction worker) + Azure Storage/Table or SQL for job state, all in Central India; Managed Identity for Graph and Document Intelligence access (no secrets in code).
- Pros: managed patching, auto-restart, scale-up path, deployment slots, native Entra ID authentication (Easy Auth), cleaner audit.
- Cons: recurring cost (Basic B1 ~$13–$15/month plus storage/bandwidth) and small migration effort from VM.
- Best for: production hardening once volumes or SLA requirements grow.

Recommendation: start on the existing Windows VM with Central India Azure DI, design the codebase so the extraction client, Graph client, and storage layer are environment-driven — enabling a lift-and-shift to App Service in Central India without rewrite.

---

## 7. Recommendation + Next Steps

**Decision: proceed with Azure AI Document Intelligence (Custom model, fallback to Layout) in Central India, hosted initially on the existing Windows VM, with Entra ID login and Microsoft Graph for SharePoint/Outlook.**

Next steps:

1. Provision Azure AI Document Intelligence resource in Central India (pay-as-you-go, S0) and a test resource group with Cost Management budget alert.
2. Collect 15–25 sample BG PDFs covering major issuing banks; redact/approve for training use.
3. Train and evaluate: (a) Layout baseline for field coverage, (b) Custom extraction model for BG number, issuing bank/branch, applicant, beneficiary, amount, issue/expiry/claim dates.
4. Build MVP pipeline: upload → DI extraction → field validation UI → Graph write to SharePoint Excel → T-7 expiry check job → Outlook email via Graph.
5. Implement Entra ID app registration (delegated/app permissions for Sites.Selected + Mail.Send), Managed Identity-ready code, Application Insights/file logging, and retry/dead-letter handling for DI throttling.
6. UAT on 1 month of live BGs in parallel with manual entry; measure field accuracy and correction rate before cutover.
7. Document runbook: VM service recovery, key rotation, cost dashboard, residency evidence (resource locations), and App Service migration plan.

---

## Sources

- Microsoft Learn — Azure AI Document Intelligence pricing and regional availability
- Microsoft Learn — Document Intelligence custom extraction and Layout model documentation
- Microsoft Learn — Microsoft Graph (SharePoint Excel, Outlook mail) and Entra ID authentication
- Google Cloud — Document AI pricing (OCR, Custom Extractor, processor hosting)
- Google Cloud — Document AI regional availability
- AWS — Textract pricing (Detect, Tables, Forms) and Mumbai region availability
- Microsoft — AI Builder / Power Platform consumption and licensing (document processing rates)
- ABBYY — Vantage SaaS pricing and data-centre information (quote-based, entry floor)
- Internal estimate — BG volume (20–25 PDFs/month, ~200 pages/month) and FX assumption ($1 = Rs 90)
