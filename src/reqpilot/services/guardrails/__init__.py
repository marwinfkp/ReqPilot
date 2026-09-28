"""Guardrails services (roadmap phase P11; architecture M11, P, Q).

* :mod:`.masking` - the unmasking map and the masking/injection step every
  ingestion path runs (``FR-ING-003``; J.2; Q.4);
* :mod:`.sessions` - opaque server-side sessions (ADR-009);
* :mod:`.deletion` - project deletion and retention (``FR-ADM-006``; P.2).

Imported by module, not re-exported here: the ingestion services import
:mod:`.masking`, and the deletion service imports much of the application, so
an eager package import would be circular.
"""
