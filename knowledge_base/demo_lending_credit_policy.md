---
title: Demo Bank Lending and Credit Policy
source_organisation: ReqPilot Demo Bank (fictional)
jurisdiction: India
version: 1.0
effective_date: 2026-01-01
doc_type: organisational_policy
authenticity: illustrative
applicability: All retail and MSME lending products, including digital loans and credit decisioning systems
---
# Demo Bank Lending and Credit Policy

> ILLUSTRATIVE DEMONSTRATION POLICY. This is a fictional organisational policy written for the ReqPilot demo. It is not a law, regulation or official guideline.

## LND-01 Fair and non-discriminatory credit decisioning
Topics: fair_lending_disclosure
Credit decisions shall be based only on documented, objective criteria related to creditworthiness, such as income, existing obligations, repayment history and bureau score. Gender, religion, caste, marital status, disability and place of birth shall not be used as model inputs or decision rules, nor shall proxies for them that have been identified through model testing. Credit models shall be tested for disparate impact across customer segments before deployment and every 6 months, and any segment approval rate below 80% of the best-performing segment shall be investigated.

## LND-02 Explainability and adverse-action reasons
Topics: fair_lending_disclosure, customer_grievance
Every declined or counter-offered application shall record the top reason codes that drove the decision. Applicants shall be informed of the decision and the principal reasons in plain language within 2 working days of the decision, together with the name of the credit bureau consulted, if any, and how to obtain a free copy of their report. Automated decisions shall be reviewable by a credit officer on request, and the review outcome shall be communicated within 7 working days.

## LND-03 Key fact statement and fee disclosure
Topics: fair_lending_disclosure, consent_privacy
Before the loan agreement is executed, the borrower shall be given a key fact statement in a standard format showing the loan amount, tenure, annual percentage rate including all fees and charges, the repayment schedule, penal charges, and grievance contact details. No fee or charge not disclosed in the key fact statement shall be levied during the life of the loan. The key fact statement shall be available in English and the borrower's preferred regional language, and the borrower's acknowledgement shall be recorded with a timestamp.

## LND-04 Credit model validation and monitoring
Topics: fair_lending_disclosure, change_management
Every credit scoring model shall be independently validated by a team not involved in its development before deployment and at least annually. Validation shall cover discriminatory power (for example a Gini coefficient of at least 0.40 on the out-of-time sample), calibration, stability and fairness. Population stability index shall be monitored monthly, and a value above 0.25 shall trigger a model review. Model changes shall follow the change management process, with a model inventory recording version, owner, approval date and validation results.

## LND-05 Cooling-off period and loan cancellation
Topics: fair_lending_disclosure, customer_grievance
Borrowers of digital loans shall have a cooling-off period of at least 3 days from disbursement during which they may cancel the loan by repaying the principal and the proportionate annual percentage rate, without any prepayment penalty. The cancellation option shall be available through the same channel used to apply. Disbursement shall be made only to the borrower's own verified bank account and never through a third-party or pool account.

## LND-06 Grievance redressal for lending
Topics: customer_grievance
Every lending product shall display the name and contact details of the nodal grievance redressal officer in the app, website and key fact statement. Complaints shall be acknowledged within 24 hours and resolved within 30 days. Complaints not resolved within 30 days, or rejected, shall be escalated automatically to the principal nodal officer, and the customer shall be informed of further escalation options. Recovery agents shall be listed on the bank's website, and complaints against recovery conduct shall be tracked separately.

## LND-07 Data use limitation for credit
Topics: consent_privacy, fair_lending_disclosure, data_retention
Lending applications shall collect only the data needed for credit assessment and loan servicing, with explicit consent for each data source such as bank statements, bureau reports or account aggregator data. Mobile apps shall not access contacts, photo galleries, call logs or file storage for credit or collection purposes. Data obtained for an application that is declined shall not be used for marketing and shall be deleted after 2 years unless required for a regulatory purpose.
