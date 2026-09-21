# E1-SYNTHETIC-v1 - adjudication sheet (run `111b163e`)

The harness proposed these (prediction, reference) pairs by span overlap or statement similarity. **Semantic
match is your decision** as the adjudicator: `match` means both state the same stakeholder need, and wording
may differ. The *suggested* column was written by the AI assistant; each entry is a suggestion until you confirm
it. The reference set is frozen and is not changed by anything here.

Suggestion rule: `match` when the prediction states the same need as the reference, without contradicting it or
adding a different obligation. Pairs marked **borderline** are worth a closer look.

| # | prediction | reference | prediction statement | reference statement | suggested | note |
|---|---|---|---|---|---|---|
| 1 | FR-LOAN-001 | R01 | The system shall enable customers to apply for a personal loan online, end to end, without visiting a branch. | The system shall allow customers to apply for a personal loan online without visiting a branch. | match |  |
| 2 | FR-LOAN-002 | R03 | The system shall enable an applicant to accept an approved loan offer electronically without a wet signature. | The system shall allow the applicant to accept a loan offer electronically. | match |  |
| 3 | FR-LOAN-003 | R05 | The system shall enable applicants to upload payslips, bank statements and proof of address into the application. | The system shall allow applicants to upload payslips, bank statements and proof of address into the application. | match |  |
| 4 | FR-LOAN-004 | R06 | The system shall accept PDF, JPEG and PNG files for uploads and reject anything else with a clear message. | The system shall accept uploaded files only in PDF, JPEG or PNG format and reject other formats with a clear message. | match |  |
| 5 | FR-LOAN-005 | R08 | The system shall enable applicants to save a half-finished application and return to it later without losing anything. | The system shall allow applicants to save a partially completed application and resume it later. | match |  |
| 6 | FR-LOAN-006 | R09 | The system shall enable applicants to see the current status of their application in the portal. | The system shall show applicants the current status of their application. | match |  |
| 7 | FR-LOAN-007 | R10 | The system shall send the applicant an email whenever the status changes. | The system shall email the applicant whenever the status of their application changes. | match |  |
| 8 | FR-LOAN-008 | R11 | The system shall send the applicant an SMS whenever the status changes. | The system shall send applicants an SMS when the status of their application changes. | match |  |
| 9 | FR-LOAN-009 | R12 | The system shall pre-fill an existing customer's details when the existing customer logs in. | The system shall pre-fill the application with an existing customer's details when they log in. | match |  |
| 10 | FR-LOAN-010 | R13 | The system shall check that every mandatory field is completed before anything is submitted. | The system shall check that every mandatory field is completed before an application is submitted. | match |  |
| 11 | FR-LOAN-011 | R14 | The system shall automatically place every new application in a loan officer's work queue. | The system shall automatically place every new application in a loan officer's work queue. | match |  |
| 12 | FR-LOAN-012 | R15 | The system shall enable loan officers to request extra documents from the applicant through the portal. | The system shall allow loan officers to request additional documents from the applicant through the portal. | match |  |
| 13 | FR-LOAN-013 | R16 | The system shall flag an application to the team lead if nothing has happened on it for five working days. | The system shall flag to the team lead any application with no activity for five working days. | match |  |
| 14 | FR-LOAN-014 | R17 | The system shall ensure that the final credit decision is made by a human underwriter and shall not make automated approvals or declines. | The system shall leave the final credit decision to a human underwriter and shall not approve or decline applications automatically. | match |  |
| 15 | FR-LOAN-015 | R18 | The system shall record the underwriter's decision together with the reason for it. | The system shall record the underwriter's decision together with the reason for it. | match |  |
| 16 | FR-LOAN-016 | R19 | The system shall pull credit reports from the credit bureau through the credit bureau's API. | The system shall retrieve credit reports from the credit bureau through its API. | match |  |
| 17 | FR-LOAN-017 | R20 | The system shall hand over an approved and accepted loan to the core banking system so the loan account can be opened. | The system shall hand approved and accepted loans over to the core banking system so the loan account can be opened. | match |  |
| 18 | FR-LOAN-018 | R21 | The system shall allow applicants to submit when the credit bureau API is unavailable and shall queue the credit check to run once the service is back. | The system shall let applicants submit while the credit bureau is unavailable and run the queued credit check when it is back. | match |  |
| 19 | FR-LOAN-019 | R22 | The system shall enable staff to sign in with the bank's existing single sign-on. | The system shall let staff sign in with the bank's existing single sign-on. | match |  |
| 20 | FR-LOAN-020 | R32 | The system shall require all staff users to use multi-factor authentication. | The system shall require multi-factor authentication for all staff users. | match |  |
| 21 | FR-LOAN-021 | R33 | The system shall require applicants to confirm their identity with a one-time passcode sent to their phone before they can see an application. | The system shall require applicants to confirm their identity with a one-time passcode before they can see an application. | match |  |
| 22 | FR-LOAN-022 | R34 | The system shall lock the account after five failed login attempts. | The system shall lock an account after five failed login attempts. | match |  |
| 23 | FR-LOAN-023 | R36 | The system shall scan uploaded documents for malware before anyone on staff opens them. | The system shall scan uploaded documents for malware before staff can open them. | match |  |
| 24 | FR-LOAN-024 | R37 | The system shall provide every member of staff with their own login. | The system shall give every member of staff an individual login. | match |  |
| 25 | FR-LOAN-025 | R38 | The system shall keep applicant data inside the portal. | The system shall keep applicant data inside the portal rather than in emailed spreadsheets. | match | **borderline** - The prediction omits the reference's gloss 'rather than in emailed spreadsheets'. The stated need (data stays inside the portal) is the same. |
| 26 | FR-LOAN-026 | R39 | The system shall time out staff sessions after 15 minutes of inactivity. | The system shall end staff sessions after 15 minutes of inactivity. | match |  |
| 27 | FR-LOAN-027 | R40 | The system shall keep loan officer sessions open for at least an hour. | The system shall keep loan officers' sessions open for at least an hour. | match |  |
| 28 | FR-LOAN-028 | R41 | The system shall allow a loan officer to see only the applications assigned to their own team. | The system shall show loan officers only the applications assigned to their own team. | match |  |
| 29 | FR-LOAN-029 | R42 | The system shall collect only the personal data needed to assess the loan. | The system shall collect only the personal data needed to assess the loan. | match |  |
| 30 | FR-LOAN-030 | R43 | The system shall capture and store the applicant's consent to a credit check before contacting the bureau. | The system shall capture and store the applicant's consent to a credit check before contacting the credit bureau. | match |  |
| 31 | FR-LOAN-031 | R44 | The system shall mask national ID numbers on staff screens, showing only the last four digits. | The system shall mask national ID numbers on staff screens, showing only the last four digits. | match |  |
| 32 | FR-LOAN-032 | R45 | The system shall let applicants request a copy of the personal data held about them. | The system shall let applicants request a copy of the personal data held about them. | match |  |
| 33 | FR-LOAN-033 | R46 | The system shall delete documents from declined applications after a while. | The system shall delete documents from declined applications after a retention period. | match |  |
| 34 | FR-LOAN-034 | R47 | The system shall show the applicant the annual percentage rate and the total cost of the credit before the applicant accepts an offer. | The system shall show the applicant the annual percentage rate and the total cost of credit before they accept an offer. | match |  |
| 35 | FR-LOAN-035 | R48 | The system shall complete identity verification and KYC checks before an application goes to an underwriter. | The system shall complete identity verification (KYC) checks before an application goes to an underwriter. | match |  |
| 36 | FR-LOAN-036 | R49 | The system shall screen every applicant against the sanctions lists. | The system shall screen every applicant against the sanctions lists. | match |  |
| 37 | FR-LOAN-037 | R51 | The system shall tell an applicant the main reasons for a declined application. | The system shall tell the applicant the main reasons when an application is declined. | match |  |
| 38 | FR-LOAN-038 | R52 | The system shall record every change to an application with who made it and when. | The system shall record every change to an application with who made it and when. | match |  |
| 39 | FR-LOAN-039 | R54 | The system shall provide the audit team with read-only access to the full history of any application. | The system shall give internal auditors read-only access to the full history of any application. | match |  |
| 40 | FR-LOAN-040 | R55 | The system shall log every time someone opens a customer's credit report. | The system shall log every access to a customer's credit report. | match |  |
| 41 | FR-LOAN-041 | R56 | The system shall provide a monthly report showing how many applications were received, approved and declined. | The system shall produce a monthly report of how many applications were received, approved and declined. | match |  |
| 42 | FR-LOAN-043 | R57 | The system shall allow business administrators to change the list of required documents without waiting for a software release. | The system shall let business administrators change the list of required documents without a software release. | match |  |
| 43 | FR-LOAN-044 | R58 | The system shall automatically alert the support team if an integration with the bureau or the core banking system fails. | The system shall automatically alert the support team when an integration with the credit bureau or the core banking system fails. | match |  |
| 44 | FR-LOAN-046 | R60 | The system shall show the credit bureau report to the underwriter inside the application screen. | The system shall show the credit bureau report to the underwriter inside the application screen. | match |  |
| 45 | NFR-LOAN-001 | R02 | The system shall provide a decision within two working days for a complete application. | The system shall support a decision within two working days for a complete application. | match | **borderline** - The prediction says the system shall 'provide' a decision. The speaker means the customer gets a decision, and R17 keeps the decision human. Same need, but the wording over-attributes it to the system. |
| 46 | NFR-LOAN-002 | R07 | The system shall enforce a size limit on each uploaded file. | The system shall limit the size of each uploaded file. | match |  |
| 47 | NFR-LOAN-003 | R23 | The system shall load each page of the application within two seconds. | The system shall load each page of the application within two seconds. | match |  |
| 48 | NFR-LOAN-004 | R25 | The system shall cope with up to 2,000 people applying at the same time. | The system shall support up to 2,000 concurrent applicants at month end. | match | **borderline** - The prediction drops 'at month end'. The core need (up to 2,000 concurrent applicants) is the same. |
| 49 | NFR-LOAN-005 | R26 | The system shall provide a fast loan officers' dashboard. | The system shall display the loan officers' dashboard quickly. | match |  |
| 50 | NFR-LOAN-006 | R27 | The system shall provide 99.9 percent availability during business hours. | The system shall be available 99.9 percent of the time during business hours. | match |  |
| 51 | NFR-LOAN-007 | R28 | The system shall be available 24 hours a day, seven days a week. | The system shall be available to customers 24 hours a day, seven days a week. | match |  |
| 52 | NFR-LOAN-008 | R29 | The system shall be offline during the Sunday night maintenance window between midnight and 4 a.m. | The system shall allow a planned maintenance window on Sunday nights between midnight and 4 a.m. | match | **borderline** - The prediction is phrased as the portal being offline in the window; the reference as allowing the window. Same need. |
| 53 | NFR-LOAN-009 | R30 | The system shall be running again from the secondary site within four hours if the primary data centre is lost. | The system shall be running again from the secondary site within four hours of losing the primary data centre. | match |  |
| 54 | NFR-LOAN-010 | R31 | The system shall not lose more than 15 minutes of applications if the primary data centre is lost. | The system shall lose no more than 15 minutes of applications after a data-centre failure. | match |  |
| 55 | NFR-LOAN-011 | R35 | The system shall encrypt all customer data when it is stored and when it travels between systems. | The system shall encrypt customer data at rest and in transit. | match |  |
| 56 | NFR-LOAN-012 | R50 | The system shall keep records of approved loans for as long as the regulations require. | The system shall keep records of approved loans for as long as the regulations require. | match |  |
| 57 | NFR-LOAN-013 | R53 | The system shall prevent anyone, including administrators, from editing or deleting audit records. | The system shall prevent anyone, including administrators, from editing or deleting audit records. | match |  |
| 58 | NFR-LOAN-014 | R59 | The system shall work well on mobile. | The system shall work well on mobile phones. | match |  |

## Items the harness did not pair (no verdict needed; listed so you can add a pair if you disagree)

| item | statement | source quote | assessment |
|---|---|---|---|
| prediction FR-LOAN-042 | The system shall allow customers to check the status of their application at any time. | "customers should be able to check the status of their application at any time" | The later restatement of the status need. R09 is already matched to FR-LOAN-006, and matching is one-to-one, so this counts as an extra prediction |
| prediction FR-LOAN-045 | The system shall support a human underwriter. | "a human underwriter" | Taken from the facilitator's playback, a distractor. It matches no reference item: a false positive |
| reference R04 | The system shall show applicants which required documents are still missing. | "Applicants never know which documents are still missing, so they phone us" | Implicit requirement; not extracted (a miss) |
| reference R24 | The system shall support 500 concurrent applicants. | "supporting 500 applicants at the same time is plenty for us." | One side of the 500 vs 2,000 conflict; not extracted (a miss) |

## Consequence if every suggestion is confirmed

58 matched of 60 predicted and 60 reference: P = R = F1 = 58/60 = 0.967. The harness computes the figure;
this line only previews it.
