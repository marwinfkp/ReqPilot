---
title: Demo Bank Data Protection and Privacy Policy
source_organisation: ReqPilot Demo Bank (fictional)
jurisdiction: India
version: 1.0
effective_date: 2026-01-01
doc_type: organisational_policy
authenticity: illustrative
applicability: All systems and processes that collect, store or process customer personal data
---
# Demo Bank Data Protection and Privacy Policy

> ILLUSTRATIVE DEMONSTRATION POLICY. This is a fictional organisational policy written for the ReqPilot demo. It is not a law, regulation or official guideline.

## DPP-01 Notice and consent
Topics: consent_privacy
Before collecting personal data, every channel shall display a clear privacy notice in English and at least one Indian regional language describing the data collected, the purpose, the retention period and how to withdraw consent. Consent shall be obtained through an affirmative action such as an unticked checkbox, shall be specific to each purpose, and shall never be bundled with acceptance of product terms. Each consent shall be recorded with customer ID, purpose, notice version, channel and timestamp. Customers shall be able to withdraw consent through the same channel with no more effort than was needed to give it, and withdrawal shall take effect within 48 hours.

## DPP-02 Purpose limitation and data minimisation
Topics: consent_privacy, data_retention
Personal data shall be processed only for the purposes stated in the privacy notice at the time of collection. Each new system shall document a data inventory listing every personal data field, its purpose and its lawful basis, and fields without a documented purpose shall not be collected. Marketing use of transaction data shall require separate opt-in consent. Analytics and reporting shall use aggregated or pseudonymised data wherever the purpose can be achieved without direct identifiers.

## DPP-03 Data principal rights
Topics: consent_privacy, customer_grievance
Customers shall be able to request access to a summary of their personal data, correction of inaccurate data, and erasure of data that is no longer required, through the mobile app, internet banking and branches. Access and correction requests shall be fulfilled within 15 days and erasure requests within 30 days, except where the data must be retained under a legal or regulatory obligation, in which case the customer shall be told the reason. Every request shall receive a tracking reference, and unresolved privacy complaints shall be escalated to the Data Protection Officer, whose contact details shall be published in the privacy notice.

## DPP-04 Retention schedule
Topics: data_retention
Customer identification and KYC records shall be retained for 5 years after the end of the business relationship. Transaction records shall be retained for 10 years from the date of the transaction. Marketing preference data shall be retained only while consent is active. Records of rejected applications shall be retained for 2 years from the date of rejection. At the end of the retention period data shall be securely deleted or irreversibly anonymised within 90 days, and deletion runs shall be logged. Data subject to a legal hold shall be excluded from deletion until the hold is released.

## DPP-05 Data localisation
Topics: data_localisation, encryption
All payment system data, including end-to-end transaction details and customer credentials collected for payment processing, shall be stored only in data centres located in India. Where a transaction has a foreign leg, any copy processed abroad shall be deleted and brought back to India within 24 hours. Cloud services used for customer or payment data shall be contracted with in-India hosting regions only, and disaster recovery sites shall also be located in India. Each system shall declare its data storage locations in the architecture document.

## DPP-06 Personal data breach notification
Topics: breach_incident_reporting, consent_privacy
Any suspected personal data breach shall be reported to the Data Protection Officer within 2 hours of detection. The Data Protection Officer shall assess the breach and, where required, notify the relevant authorities within 72 hours of detection. Affected customers shall be informed without undue delay and no later than 72 hours after the breach is confirmed, with a description of the breach, likely consequences, and the steps they should take. A breach register shall record every breach, its impact and the corrective actions taken.

## DPP-07 Masking and pseudonymisation in non-production environments
Topics: consent_privacy, secure_development
Production personal data shall not be copied to development, test or training environments. Where production-like data is needed for testing, it shall be masked or pseudonymised using irreversible techniques so that no individual can be identified, and names, mobile numbers, account numbers, PAN and Aadhaar numbers shall always be replaced. Synthetic data shall be preferred for automated test suites. Any exception requires written approval from the Data Protection Officer, a time limit of no more than 30 days and access logging.

## DPP-08 Sharing with third parties
Topics: consent_privacy, third_party_api_security
Personal data shall be shared with a third party only under a written agreement that limits use to the stated purpose, requires equivalent security controls, prohibits onward sharing without approval and grants the bank audit rights. Only the minimum data fields needed for the service shall be transmitted. Every data-sharing interface shall be listed in the data inventory, and each outbound transfer of personal data shall be logged with recipient, data categories and volume. Third-party access shall be reviewed annually.

## DPP-09 Children's data and special categories
Topics: consent_privacy, kyc_cdd
Accounts for customers below 18 years of age shall be opened and operated only with verifiable consent of a parent or lawful guardian. Behavioural tracking, profiling and targeted advertising directed at minors shall not be performed. Health, biometric and other special categories of data shall be collected only where strictly necessary, shall be encrypted at field level, and shall be accessible only to roles explicitly authorised in the access control matrix. Biometric templates used for authentication shall remain on the customer device or in the hardware security module and shall never be stored as raw images.
