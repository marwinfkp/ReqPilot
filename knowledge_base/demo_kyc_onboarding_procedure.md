---
title: Demo Bank KYC and Customer Onboarding Procedure
source_organisation: ReqPilot Demo Bank (fictional)
jurisdiction: India
version: 1.0
effective_date: 2026-01-01
doc_type: organisational_policy
authenticity: illustrative
applicability: All account opening, customer onboarding and customer due diligence processes
---
# Demo Bank KYC and Customer Onboarding Procedure

> ILLUSTRATIVE DEMONSTRATION POLICY. This is a fictional organisational procedure written for the ReqPilot demo. It is not a law, regulation or official guideline.

## KYC-01 Customer due diligence
Topics: kyc_cdd
No account shall be opened or loan disbursed until customer due diligence is complete. Customer due diligence shall include verification of identity and current address, collection of PAN or the approved alternative declaration, and capture of occupation and expected annual transaction volume. For non-individual customers, beneficial owners holding 10% or more shall be identified and verified. Onboarding systems shall prevent account activation while any mandatory CDD field is missing or unverified.

## KYC-02 Identity verification including video-based verification
Topics: kyc_cdd, authentication
Identity shall be verified through Aadhaar-based e-KYC with customer consent, through officially valid documents verified against issuer databases where available, or through video-based customer identification. Video-based identification sessions shall be conducted live by a trained bank official, shall include a liveness check and a random question, shall capture geolocation confirming the customer is in India, and shall be recorded with a timestamp. Recordings shall be stored encrypted in India. Accounts opened through OTP-based e-KYC without in-person or video verification shall be subject to reduced balance and credit limits until full KYC is completed within 12 months.

## KYC-03 Risk categorisation
Topics: kyc_cdd, fraud_monitoring
Every customer shall be assigned a money-laundering risk category of Low, Medium or High at onboarding using documented criteria including occupation, geography, product type, expected transaction volume and politically exposed person status. High-risk customers shall require enhanced due diligence and approval by a senior official before account activation. The risk category shall be re-evaluated automatically when material profile changes occur or when transaction monitoring generates repeated alerts.

## KYC-04 Periodic KYC update
Topics: kyc_cdd, consent_privacy
KYC information shall be updated at least once every 2 years for high-risk customers, every 8 years for medium-risk customers and every 10 years for low-risk customers. Customers shall be notified at least 30 days before their KYC update is due, through SMS, email and in-app message. Customers with no change in KYC information shall be able to submit a self-declaration through the mobile app or internet banking. Accounts with KYC overdue by more than 90 days shall be placed under partial freeze after due notice.

## KYC-05 Sanctions and AML screening
Topics: kyc_cdd, fraud_monitoring
All customers, beneficial owners and authorised signatories shall be screened against the bank's consolidated sanctions and watch lists at onboarding and again within 24 hours of every list update. Potential matches shall be held for review by the compliance team and shall not be auto-cleared. Transaction monitoring scenarios shall flag cash deposits above INR 10,00,000 in a month, structuring patterns and rapid movement of funds, and suspicious transaction alerts shall be dispositioned within 7 days.

## KYC-06 KYC record keeping
Topics: kyc_cdd, data_retention, audit_logging
Copies of identity documents, e-KYC responses, video verification recordings and risk assessments shall be retained for at least 5 years after the business relationship ends or the account is closed, whichever is later. Records shall be stored so that they can be produced to authorised auditors and authorities within 2 working days of a request. All changes to customer KYC data shall be logged with the user who made the change, the previous value and the timestamp.

## KYC-07 Onboarding consent
Topics: kyc_cdd, consent_privacy
Explicit consent shall be captured before performing Aadhaar-based authentication, fetching credit bureau data, or retrieving data from any central KYC registry. The consent screen shall state the purpose, the data to be fetched and the retention period, and shall offer an alternative verification method for customers who do not wish to use Aadhaar. Consent artefacts shall be retained with the customer's KYC record for the same retention period.
