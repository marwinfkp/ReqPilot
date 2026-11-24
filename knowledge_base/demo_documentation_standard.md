---
title: Demo Bank Project Documentation Standard
source_organisation: ReqPilot Demo Bank (fictional)
jurisdiction: India
version: 1.0
effective_date: 2026-01-01
doc_type: engineering_guideline
authenticity: illustrative
applicability: Requirement, design and test documentation of software projects
---
# Demo Bank Project Documentation Standard

> ILLUSTRATIVE DEMONSTRATION STANDARD. This is a fictional internal documentation standard written for the ReqPilot demo, in ReqPilot's own words.

## DOC-01 User stories
User stories shall follow the form "As a <role>, I want <capability> so that <benefit>". The role shall be a real user or stakeholder role, such as customer, loan officer or compliance officer, rather than "the system". Each story shall reference the requirement ID it implements.

## DOC-02 Acceptance criteria
Acceptance criteria shall be written in Given/When/Then form and shall be testable. They shall keep the numbers, limits and conditions of the requirement exactly, and shall include at least one criterion for invalid input or a failure case. Where a requirement does not yet state a target, the criterion shall contain a visible placeholder such as "target to be agreed" instead of an invented value.

## DOC-03 Use case descriptions
Use cases shall name the primary actor, the preconditions, the main flow as numbered steps, the alternate and exception flows and the postconditions, and shall list the requirement IDs they cover.

## DOC-04 Requirements specification structure
The software requirements specification shall contain the purpose and scope, stakeholders, glossary, functional requirements, non-functional requirements, business rules, constraints, assumptions and open issues. Each requirement shall have a unique ID, a priority, a source and a status.

## DOC-05 Traceability matrix
The requirements traceability matrix shall link each requirement to its source, the regulatory and security controls it implements, its user stories, acceptance criteria, test cases and the design or workflow element that realises it. Missing links shall be reported before each approval gate.

## DOC-06 Versioning and approval of documents
Every controlled document shall have a version number, a change history, an owner and a record of who approved it and when. Superseded versions shall be kept for audit.
