---
title: Demo Bank Financial Services Domain Glossary
source_organisation: ReqPilot Demo Bank (fictional)
jurisdiction: India
version: 1.0
effective_date: 2026-01-01
doc_type: domain_glossary
authenticity: illustrative
applicability: Shared vocabulary for requirements of Indian banking and financial applications
---
# Demo Bank Financial Services Domain Glossary

> ILLUSTRATIVE DEMONSTRATION GLOSSARY. Plain-language definitions written for the ReqPilot demo, in ReqPilot's own words. The "Typical requirement categories" line is a hint for analysts, not a rule.

## GLO-01 UPI - Unified Payments Interface
UPI is India's real-time interbank payment system in which customers pay or collect money with a virtual payment address or a QR code from a mobile application. Requirements mentioning UPI usually involve integration with the payment network, customer authentication with a UPI PIN, transaction limits, real-time availability and dispute handling. Typical requirement categories: integration, security, regulatory, availability, performance.

## GLO-02 IMPS / NEFT / RTGS - Interbank fund transfer systems
IMPS is an instant, round-the-clock interbank transfer service; NEFT settles transfers in frequent batches; RTGS settles high-value transfers individually in real time. Requirements that mention them usually concern beneficiary management, transfer limits, settlement, reconciliation and failed-transaction reversal. Typical requirement categories: integration, regulatory, reliability, auditability.

## GLO-03 KYC / CKYC / V-CIP - Customer identification
KYC (know your customer) is the process of identifying and verifying a customer before and during the relationship. CKYC is a central registry of KYC records shared between regulated entities, and V-CIP is video-based customer identification. Requirements that mention them usually involve identity documents, consent, record keeping and periodic updates. Typical requirement categories: regulatory, privacy, data management, security.

## GLO-04 AML / CFT - Anti-money laundering screening
AML and CFT controls detect and report money laundering and terrorist financing. Requirements usually cover screening customers and transactions against sanctions and watch lists, monitoring unusual activity, raising alerts and keeping records for the period required by law. Typical requirement categories: regulatory, auditability, data management.

## GLO-05 PEP - Politically exposed person
A politically exposed person is someone entrusted with a prominent public function, or a close relative or associate of such a person. Onboarding and monitoring requirements usually call for enhanced due diligence and senior approval for these customers. Typical requirement categories: regulatory, business.

## GLO-06 EMI / APR / KFS - Loan repayment and disclosure terms
EMI is the equated monthly instalment a borrower repays; APR is the annual percentage rate expressing the total cost of credit; the KFS (key fact statement) is the standardised summary of a loan's terms given to the borrower before signing. Requirements that mention them usually concern calculation accuracy and disclosure. Typical requirement categories: regulatory, business, functional.

## GLO-07 CBS - Core banking system
The core banking system holds customer accounts, balances and the general ledger of the bank. Requirements that integrate with it usually concern interface contracts, batch and real-time posting, data migration and the behaviour when the core system is unavailable. Typical requirement categories: integration, reliability, data management.

## GLO-08 Maker-checker - Dual authorisation
Maker-checker is a control in which one user prepares (makes) a sensitive transaction or change and a different user approves (checks) it before it takes effect. Requirements with maker-checker usually concern role separation, approval workflows and audit trails. Typical requirement categories: security, auditability, operational.

## GLO-09 OTP / MFA - One-time password and multi-factor authentication
An OTP is a single-use code sent to or generated on a registered device; MFA combines two or more independent factors, such as something the customer has and something the customer knows. Requirements usually state when step-up authentication is needed and how long codes stay valid. Typical requirement categories: security.

## GLO-10 TAT / Chargeback - Dispute and reversal handling
TAT is the turnaround time within which a failed or disputed transaction must be resolved or reversed; a chargeback is a card transaction reversed after a customer dispute. Requirements usually concern time limits, customer compensation and status notifications. Typical requirement categories: regulatory, operational, business.

## GLO-11 RTO / RPO - Recovery objectives
The recovery time objective is the maximum acceptable time to restore a service after a disruption; the recovery point objective is the maximum acceptable period of data loss. Requirements stating them belong to business continuity and disaster recovery. Typical requirement categories: availability, reliability, operational.

## GLO-12 PII / SPDI - Personal and sensitive personal data
PII is information that identifies a person; sensitive personal data includes items such as financial information, passwords and biometric data. Requirements that process them usually concern consent, purpose limitation, masking, retention and access control. Typical requirement categories: privacy, security, data management, regulatory.

## GLO-13 Credit bureau / CIC - Credit information company
A credit information company collects repayment histories and provides credit reports and scores to lenders. Requirements that use credit bureau data usually concern borrower consent, secure integration, data accuracy and explaining decisions to applicants. Typical requirement categories: integration, privacy, regulatory.

## GLO-14 PA / PG - Payment aggregator and payment gateway
A payment aggregator lets merchants accept payments from customers and settles the funds to them; a payment gateway provides the technology to route and authorise online payments. Requirements usually concern merchant onboarding, settlement timelines, card data protection and refunds. Typical requirement categories: integration, security, regulatory.
