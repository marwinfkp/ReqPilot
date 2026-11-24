---
title: Demo Bank Technology and Operational Risk Management Guideline
source_organisation: ReqPilot Demo Bank (fictional)
jurisdiction: India
version: 1.0
effective_date: 2026-01-01
doc_type: organisational_policy
authenticity: illustrative
applicability: Risk assessment of technology projects and IT-enabled services
---
# Demo Bank Technology and Operational Risk Management Guideline

> ILLUSTRATIVE DEMONSTRATION GUIDELINE. This is a fictional internal risk guideline written for the ReqPilot demo, in ReqPilot's own words. It is not a regulation.

## RSK-01 Project risk register
Every technology project shall maintain a risk register from the requirements phase onwards. Each risk shall have a description, a category, a likelihood and impact rating on a five-point scale, an owner and a mitigation plan. The register shall be reviewed at every phase gate.
Topics: change_management

## RSK-02 Risk acceptance
Risks rated high or critical shall not be accepted by the project team alone. Acceptance requires the approval of the business owner and, for security and compliance risks, the chief information security officer or the compliance officer. Accepted risks shall be reviewed at least every six months.

## RSK-03 Third-party and outsourcing risk
Before a project depends on an external service provider, partner API or cloud service, the provider shall be assessed for security, financial stability, data location and business continuity. Contracts shall include audit rights, incident notification obligations and an exit plan. Concentration on a single critical provider shall be recorded as a risk.
Topics: third_party_api_security

## RSK-04 Legacy and integration risk
Projects that depend on legacy platforms such as the core banking system shall assess interface stability, data quality, batch windows and the behaviour when the legacy system is unavailable. Data migrations shall be rehearsed and reconciled before go-live.

## RSK-05 Cyber-security risk
Applications exposed to the internet or handling customer funds shall be assessed for cyber threats such as account takeover, fraud, data theft and denial of service. Threat modelling shall be performed during design, and the resulting risks shall be tracked in the risk register until their controls are tested.
Topics: secure_development, fraud_monitoring

## RSK-06 Change-related risk
Frequent or large changes to production systems increase the risk of outages. Changes shall be approved through the change process, tested in a production-like environment and deployed with a tested rollback plan; failed changes shall be analysed for their root cause.
Topics: change_management

## RSK-07 Model risk
Analytical and machine-learning models used for credit, fraud or pricing decisions shall be validated before use and monitored for accuracy, bias and drift. Model limitations and the data used for training shall be documented, and decisions affecting customers shall remain explainable.

## RSK-08 Business continuity risk
Critical services shall have documented recovery time and recovery point objectives, tested recovery procedures and alternate processing arrangements. The impact of an outage on customers, payments and regulatory reporting shall be assessed when a new service is designed.
Topics: business_continuity

## RSK-09 Delivery and schedule risk
Fixed deadlines, fixed budgets, unclear requirements and unresolved stakeholder conflicts are recognised sources of delivery risk. They shall be recorded in the risk register with mitigations such as phased releases, early prototyping and prioritisation of mandatory requirements.
