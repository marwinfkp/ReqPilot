---
title: Demo Bank SDLC and Engineering Practices Handbook
source_organisation: ReqPilot Demo Bank (fictional)
jurisdiction: India
version: 1.0
effective_date: 2026-01-01
doc_type: engineering_guideline
authenticity: illustrative
applicability: Choice of delivery model and engineering practices for software projects
---
# Demo Bank SDLC and Engineering Practices Handbook

> ILLUSTRATIVE DEMONSTRATION HANDBOOK. This is a fictional internal handbook written for the ReqPilot demo, in ReqPilot's own words. It summarises generally known characteristics of delivery models. It does not decide which model a project must use.

## SDLC-01 Choosing a delivery model
The delivery model shall be chosen from the characteristics of the project: stability of the requirements, regulatory criticality, security risk, complexity, dependence on legacy systems, the expected frequency of change and releases, stakeholder availability and the consequences of failure. The choice and its reasons shall be recorded and approved by the project board. No single model is mandatory; combinations are acceptable when they are justified.

## SDLC-02 Agile delivery
Agile delivery builds the product in short iterations with a prioritised backlog and frequent stakeholder feedback. It suits products whose requirements are expected to change and whose stakeholders are available throughout the project. In regulated systems, Agile teams shall add explicit sign-off points for compliance and security and keep a requirement baseline for regulated features.

## SDLC-03 Waterfall delivery
Waterfall delivery completes requirements, design, build, test and deployment in sequence, with a sign-off at the end of each phase. It suits small or well-understood systems with stable requirements and a single planned release. Its main weakness is that defects, security issues and misunderstandings are often discovered late, when they are expensive to fix.

## SDLC-04 V-Model delivery
The V-Model pairs every specification level with a test level: requirements with acceptance tests, system design with system tests, architecture with integration tests and module design with unit tests. It suits systems with high verification and audit needs, such as calculations, settlement and regulatory reporting, where full traceability from requirements to tests is expected.

## SDLC-05 Spiral delivery
The Spiral model runs risk-driven cycles: each cycle sets objectives, analyses the biggest risks, often with prototypes, develops and verifies an increment and plans the next cycle. It suits large, novel or high-risk systems, for example those using new technology or analytical models, where uncertainty must be reduced before full commitment.

## SDLC-06 DevSecOps delivery
DevSecOps combines continuous integration and delivery with security activities automated in every stage: static analysis, dependency scanning, dynamic testing and policy checks run on every change, and production is monitored continuously. It suits systems that are released frequently and face a high security risk. It requires mature automation, and formal regulatory sign-offs must be added explicitly as release gates.

## SDLC-07 Hybrid approaches in regulated projects
Regulated financial projects often combine models: for example Agile sprints for evolving features inside V-Model verification levels and formal compliance gates, or Agile delivery on a DevSecOps pipeline with release gates for security and compliance approval. A hybrid approach shall define which parts of the scope follow which practice, and who approves the gates.

## SDLC-08 Phase gates and approvals
Every delivery model shall include human approval gates for the requirement baseline, the architecture and security design, test completion, user acceptance and production release. Regulatory interpretations shall be approved by the compliance officer and residual security risks by the security officer. Automated pipeline results support, but never replace, these approvals.

## SDLC-09 Environments and test data
Development and test environments shall be separated from production. Production personal data shall not be used in non-production environments unless it is masked or synthetic. Access to production shall be limited to authorised operations staff, and deployments shall be performed through the approved pipeline.

## SDLC-10 Definition of Done
A work item is done when its acceptance criteria are met, its code has been reviewed, automated tests pass, security scans show no open critical findings, documentation and the traceability matrix are updated, and the product owner has accepted it.
