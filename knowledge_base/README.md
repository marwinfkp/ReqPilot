# ReqPilot Knowledge Base

This folder holds the seed documents that ReqPilot uses to map extracted requirements to compliance evidence. Every document is a Markdown file with YAML-like front matter followed by sections that start with `## <SECTION-REF> <Title>` and a `Topics:` line.

## What is in this folder

| File | Section prefix | doc_type | authenticity |
|---|---|---|---|
| `demo_information_security_policy.md` | ISP- | security_standard | illustrative |
| `demo_data_protection_policy.md` | DPP- | organisational_policy | illustrative |
| `demo_payments_transaction_standard.md` | PAY- | organisational_policy | illustrative |
| `demo_kyc_onboarding_procedure.md` | KYC- | organisational_policy | illustrative |
| `demo_lending_credit_policy.md` | LND- | organisational_policy | illustrative |
| `demo_it_operations_change_standard.md` | OPS- | organisational_policy | illustrative |
| `india_regulatory_reference_catalogue.md` | REF- | reference | reference |

### Illustrative documents (fictional)

The six `demo_*.md` files are **fictional internal policies of "ReqPilot Demo Bank"**, an organisation that does not exist. They were written for this academic demo so that ReqPilot has concrete, testable clauses to match against (for example a 10-minute session idle timeout, 10-year transaction retention, or an RTO of 2 hours and RPO of 15 minutes). They are not laws, regulations or official guidelines. The numbers in them are plausible but invented, and they do not reproduce any regulator's text. ReqPilot should label any evidence drawn from them as "illustrative".

### Reference catalogue

`india_regulatory_reference_catalogue.md` lists real Indian laws, RBI directions and circulars, CERT-In directions, NPCI guidelines and industry standards (PCI DSS, ISO/IEC 27001). Each entry gives the official title, issuing body, approximate date, where to obtain it, and a short scope summary in our own words. **It contains no regulatory text and no clause numbers.** Treat a match against this catalogue as "this regulation is probably relevant, go and read it", not as clause-level evidence. Dates marked "verify with issuer" should be checked, and issuers regularly amend or consolidate their instruments.

## Adding official documents

To get clause-level evidence from real regulations:

1. Download the official document (usually a PDF) directly from the issuer's website listed below. Do not use third-party copies or summaries.
2. Check that it is the current version (look for later amendments, consolidated versions or repeal notices on the issuer's site).
3. Open the ReqPilot web app, go to the **Knowledge Base** page, and upload the file.
4. Set the metadata: authenticity **official**, doc_type **regulation** (or **security_standard** for standards such as PCI DSS), the issuing organisation, jurisdiction, version and effective date.
5. Review the sections and topic tags that ReqPilot proposes, and correct them where needed.

Official documents uploaded this way take precedence over the illustrative policies and the reference catalogue when ReqPilot reports compliance evidence. Respect each issuer's terms of use; some standards (for example ISO/IEC 27001 and PCI DSS) are licensed for internal use only and should not be redistributed with the project.

## Official issuer websites

- Reserve Bank of India (RBI): https://www.rbi.org.in
- Ministry of Electronics and Information Technology (MeitY): https://www.meity.gov.in
- Indian Computer Emergency Response Team (CERT-In): https://www.cert-in.org.in
- India Code (official repository of central Acts): https://www.indiacode.nic.in
- National Payments Corporation of India (NPCI): https://www.npci.org.in
- Unique Identification Authority of India (UIDAI): https://uidai.gov.in
- PCI Security Standards Council: https://www.pcisecuritystandards.org
- International Organization for Standardization (ISO): https://www.iso.org

## Topic keys

Every section's `Topics:` line uses only these keys: authentication, access_control, encryption, audit_logging, data_localisation, consent_privacy, data_retention, breach_incident_reporting, transaction_integrity, fraud_monitoring, kyc_cdd, customer_grievance, fair_lending_disclosure, business_continuity, third_party_api_security, change_management, secure_development, performance_capacity.
