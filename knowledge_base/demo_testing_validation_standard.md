---
title: Demo Bank Testing and Validation Standard
source_organisation: ReqPilot Demo Bank (fictional)
jurisdiction: India
version: 1.0
effective_date: 2026-01-01
doc_type: engineering_guideline
authenticity: illustrative
applicability: Verification and validation of all customer-facing and internal financial applications
---
# Demo Bank Testing and Validation Standard

> ILLUSTRATIVE DEMONSTRATION STANDARD. This is a fictional internal testing standard written for the ReqPilot demo, in ReqPilot's own words. It is not an official testing standard.

## TST-01 Requirement-based test coverage
Every approved requirement shall be covered by at least one test case, and every high-priority requirement by positive and negative test cases. Test cases shall reference the requirement IDs they verify, so that coverage can be reported from the traceability matrix before the test completion review.

## TST-02 Test levels
Testing shall be performed at unit, integration, system and acceptance level. Unit tests verify individual components, integration tests verify interfaces between components and external systems, system tests verify the complete application against functional and non-functional requirements, and acceptance tests confirm that business needs are met.

## TST-03 Security testing
Security requirements shall be verified with negative and abuse-case tests, such as attempts to bypass authentication or authorisation, to replay transactions or to inject malicious input. Applications that handle customer or payment data shall undergo static and dynamic security testing during development and an independent penetration test before the first production release and after major changes.

## TST-04 Performance and capacity testing
Performance requirements shall be verified with load tests at the stated peak volume and with stress tests beyond it. Results shall be measured against the stated targets, such as the 95th-percentile response time, throughput and error rate, in an environment comparable to production.

## TST-05 Resilience and disaster-recovery testing
Availability requirements shall be verified with failover tests and a disaster-recovery drill that measures the achieved recovery time and recovery point against the stated objectives. Backups shall be restored in a test at least twice a year.

## TST-06 Transaction integrity testing
Applications that move money shall be tested for idempotency and duplicate prevention, correct handling of timeouts and partial failures, reversal of failed transactions within the stated turnaround time, and reconciliation of test transactions with settlement reports.

## TST-07 Privacy and data protection testing
Requirements involving personal data shall be verified by tests of consent capture and withdrawal, masking of personal data in user interfaces and logs, enforcement of access rights and deletion or anonymisation at the end of the retention period.

## TST-08 Regulatory control verification
Each control mapped to a regulatory or policy obligation shall have a test or inspection that produces evidence of its operation, such as a test report, screenshot or log extract. This evidence shall be retained and made available to the compliance officer and auditors.

## TST-09 User acceptance testing
User acceptance testing shall be performed by business users against the acceptance criteria of the approved requirements, using realistic but synthetic or masked data. The product owner signs off acceptance; open defects of high severity block the sign-off.

## TST-10 Regression and test automation
Automated regression tests shall run for every change before it is promoted to the next environment. Tests for high-risk functions, such as authentication, payments and interest calculation, shall be part of the automated regression suite.
