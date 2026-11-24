---
title: Demo Bank Requirements Engineering Guideline
source_organisation: ReqPilot Demo Bank (fictional)
jurisdiction: India
version: 1.0
effective_date: 2026-01-01
doc_type: engineering_guideline
authenticity: illustrative
applicability: Business, functional and non-functional requirements of all software projects
---
# Demo Bank Requirements Engineering Guideline

> ILLUSTRATIVE DEMONSTRATION GUIDELINE. This is a fictional internal guideline written for the ReqPilot demo, in ReqPilot's own words. It is not an official standard. It follows widely used good practice for writing requirements.

## REQ-01 Unambiguous wording
Each requirement shall have exactly one interpretation. Subjective words such as fast, user-friendly, robust, secure enough, flexible, minimal or as soon as possible shall not be used on their own, because two readers will understand them differently and no test can decide whether they are met. Where such a word is needed, it shall be replaced by the measurable condition it stands for, for example a response time, an error rate or a number of steps.

## REQ-02 Measurable quality attributes
Every performance, availability, reliability, capacity and usability requirement shall state a target value, a unit and the conditions under which it is measured, for example "95% of balance enquiries complete within 2 seconds at 500 concurrent users". A quality attribute without a target value shall be recorded as incomplete and clarified with the requirement owner before the baseline is approved.

## REQ-03 Completeness
A requirement is complete when it names the actor, the action, the object, the conditions and the expected result, including the behaviour for invalid input and failure cases. Placeholders such as TBD, TBC, to be decided or etc. shall be resolved before the requirement baseline is approved. Limits, thresholds and time periods mentioned in business rules shall be stated as exact values.

## REQ-04 Consistency and conflict resolution
Requirements shall not contradict each other or applicable policies. When two requirements state different values for the same subject, such as two different session timeouts or retention periods, the conflict shall be recorded and resolved with the requirement owners; neither requirement shall be changed silently. Conflicts between stakeholders shall be escalated to the product owner for a documented decision.

## REQ-05 Verifiability and acceptance criteria
Every requirement shall be verifiable by test, inspection, analysis or demonstration. High-priority requirements shall have at least one acceptance criterion written in Given/When/Then form that preserves the requirement's numbers and conditions. Absolute targets such as 100% availability, zero defects or instant processing cannot be verified and shall be replaced by realistic, measurable targets.

## REQ-06 Atomicity and duplicates
Each requirement shall describe one capability or constraint so it can be prioritised, implemented and tested on its own. Statements joined by "and" or "or" that describe several capabilities shall be split. Duplicate requirements shall be merged into one, keeping the sources of both for traceability.

## REQ-07 Source and ownership
Every requirement shall record the stakeholder or document it came from and an owner who can answer questions about it. Requirements without a source cannot be validated and shall be confirmed with a business owner before the baseline is approved.

## REQ-08 Terminology and glossary
Domain terms, abbreviations and acronyms shall be defined in the project glossary the first time they are used. A requirement that uses an undefined term shall be clarified, because misunderstood terms lead to wrong implementations.

## REQ-09 Feasibility
Requirements shall be achievable within known technical, regulatory, budget and schedule constraints. Targets that depend on external systems, such as response times that include a partner bank or a credit bureau, shall state which part of the time the system itself is responsible for.

## REQ-10 Requirement change control
After the baseline is approved, changes to requirements shall be proposed as change requests with a reason, assessed for impact on design, tests and compliance, and approved by the product owner before they are applied. The original requirement and every approved change shall be kept for audit.
