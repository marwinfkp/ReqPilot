---
title: Demo Bank IT Operations and Change Management Standard
source_organisation: ReqPilot Demo Bank (fictional)
jurisdiction: India
version: 1.0
effective_date: 2026-01-01
doc_type: organisational_policy
authenticity: illustrative
applicability: All production systems, infrastructure and release processes operated by or for the bank
---
# Demo Bank IT Operations and Change Management Standard

> ILLUSTRATIVE DEMONSTRATION POLICY. This is a fictional organisational standard written for the ReqPilot demo. It is not a law, regulation or official guideline.

## OPS-01 Change approval board
Topics: change_management
All changes to production systems shall be raised as change requests with a description, risk assessment, test evidence, implementation plan and rollback plan. Normal changes shall be approved by the Change Advisory Board, which meets weekly. Standard pre-approved changes, such as automated deployments through an accredited CI/CD pipeline with passing quality gates, may proceed without individual board approval. Emergency changes shall be approved by the duty IT head and reviewed retrospectively by the board within 3 working days.

## OPS-02 Release management and rollback
Topics: change_management, business_continuity
Every production release shall be deployed from a versioned, immutable build artefact that has passed functional, regression and security testing in a pre-production environment matching production configuration. Each release shall have a tested rollback procedure that restores the previous version within 30 minutes. Releases to customer-facing systems shall be scheduled outside peak hours of 09:00 to 21:00 IST unless deployed using zero-downtime techniques such as blue-green or canary deployment. Post-release verification shall be completed within 1 hour of deployment.

## OPS-03 Segregation of duties
Topics: access_control, change_management
Developers shall not have write access to production systems or production data. The person who writes a code change shall not be the person who approves it for release. Deployments to production shall be executed by the release pipeline or by an operations engineer who did not author the change. Database administrators shall not be able to modify application audit logs, and all privileged sessions shall be recorded.

## OPS-04 Availability targets and monitoring
Topics: business_continuity, performance_capacity
Critical customer-facing systems shall achieve monthly availability of at least 99.95%, excluding scheduled maintenance windows notified to customers at least 48 hours in advance, which shall not exceed 4 hours per month. Each system shall have synthetic transaction monitoring from outside the data centre at least every 5 minutes, and alerts shall reach the on-call engineer within 2 minutes of a threshold breach. Major incidents affecting customers shall be communicated through the app, website and SMS within 30 minutes.

## OPS-05 Capacity and performance testing
Topics: performance_capacity
Before go-live and before every major release, each customer-facing system shall be load tested at no less than 2 times the observed peak transaction volume of the previous 12 months. Response time targets shall be defined per transaction type, with a default of 2 seconds at the 95th percentile for interactive requests. Capacity utilisation of CPU, memory and storage shall be reviewed monthly, and expansion shall be initiated when sustained peak utilisation exceeds 70%.

## OPS-06 Backup and restore testing
Topics: business_continuity, data_retention
Databases of critical systems shall be backed up with full backups daily and transaction log backups at least every 15 minutes. Backups shall be encrypted, stored in at least two locations within India, and at least one copy shall be held offline or in immutable storage. Restore tests shall be performed for each critical system at least quarterly, and the restore time achieved shall be recorded and compared with the system's recovery time objective.

## OPS-07 Production access control
Topics: access_control, audit_logging
Access to production servers, databases and cloud consoles shall be granted only through the privileged access management system, using just-in-time access approved by the system owner for a maximum of 8 hours per request. Direct interactive access using shared or root accounts shall be disabled. All privileged sessions shall be recorded and the recordings retained for 1 year, and privileged access logs shall be reviewed weekly by the information security team.
