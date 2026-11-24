"""Agent 4 - Compliance and Regulatory Agent (retrieval-grounded).

1. Retrieve the most relevant knowledge-base passages for every requirement
   (allowlisted documents of the project's jurisdiction only).
2. Map requirements to controls:
   * rules mode  - a passage is mapped when it shares a compliance topic with the
     requirement and has enough keyword overlap; the evidence is the passage's
     most relevant sentence (verbatim).
   * OpenAI mode - the model decides which retrieved passages really support a
     requirement and must quote the passage. Every quote is checked against the
     source text; unverifiable quotes are flagged, never presented as evidence.
3. Detect compliance gaps: topics a regulated application in this domain is
   expected to address (e.g. data localisation for payments) that no
   requirement covers.

The agent never makes legal determinations. Evidence from "illustrative" demo
policies and document-level "reference" entries is labelled as such, and
every mapping waits for compliance-officer approval.
"""
from pydantic import BaseModel

from app import llm
from app.agents import check_reference, finding, valid_ids
from app.agents.clarification_context import resolve_topic_gap, supplementary_text
from app.rules import TOPICS, detect_topics, expected_topics
from app.security import mask_sensitive, wrap_untrusted
from app.services import rag

_CORE_TOPICS = {"authentication", "data_localisation", "transaction_integrity", "kyc_cdd", "consent_privacy",
                "audit_logging", "fair_lending_disclosure"}


def _mapping(req: dict, hit: dict, evidence: str, verified: bool, confidence: float, rationale: str,
             generated_by: str) -> dict:
    confidence = min(confidence, 0.5) if hit["authenticity"] == "reference" else confidence
    return {
        "requirement_id": req["req_id"], "document_id": hit["document_id"], "chunk_id": hit["chunk_id"],
        "document_name": hit["document_name"], "doc_type": hit["doc_type"], "authenticity": hit["authenticity"],
        "control_ref": hit["section_ref"], "control_title": hit["title"], "jurisdiction": hit["jurisdiction"],
        "version": hit["version"], "evidence_text": evidence, "citation_verified": verified,
        "rationale": rationale, "confidence": round(confidence, 2),
        "needs_verification": hit["authenticity"] == "reference" or not verified or confidence < 0.6,
        "generated_by": generated_by,
    }


def map_with_rules(req: dict, hits: list[dict]) -> list[dict]:
    req_topics = set(detect_topics(supplementary_text(req)))
    mappings = []
    for hit in hits:
        shared = req_topics & set(hit["topics"])
        if not shared or hit["score"] < 0.3:
            continue
        evidence = rag.best_sentence(hit["text"], req["statement"])
        labels = ", ".join(TOPICS[t]["label"] for t in sorted(shared) if t in TOPICS)
        mappings.append(_mapping(req, hit, evidence, True, 0.35 + 0.5 * hit["score"],
                                 f"Shared compliance topic(s): {labels}; keyword relevance {hit['score']:.2f}.",
                                 "rules"))
        if len(mappings) == 2:
            break
    return mappings


class ControlMapping(BaseModel):
    requirement_id: str
    chunk_id: int
    evidence_quote: str
    rationale: str
    confidence: float


class ComplianceGap(BaseModel):
    title: str
    explanation: str
    requirement_ids: list[str]
    supporting_chunk_ids: list[int]


class ComplianceOutput(BaseModel):
    mappings: list[ControlMapping]
    gaps: list[ComplianceGap]


_INSTRUCTIONS = """Decide which retrieved knowledge-base passages genuinely support (map to) each \
requirement as a regulatory/policy/security control. Only map when the passage is clearly relevant. \
evidence_quote must be copied word-for-word from the passage (one sentence). confidence 0-1. \
Also report gaps: obligations suggested by the passages that the requirements do not address \
(supporting_chunk_ids must come from the passages; leave empty if you have no passage evidence). \
Passages marked authenticity=illustrative are fictional demo policies; authenticity=reference are \
document-level summaries, not legal text - never treat them as verified law."""


def _map_batch_llm(batch: list[tuple[dict, list[dict]]], ctx: dict) -> tuple[ComplianceOutput, dict]:
    passages: dict[int, dict] = {}
    for _, hits in batch:
        for hit in hits:
            passages[hit["chunk_id"]] = hit
    req_text = "\n".join(f"{req['req_id']}: {mask_sensitive(req['statement'])[0]}" for req, _ in batch)
    passage_text = "\n\n".join(
        f"[chunk_id={h['chunk_id']}] {h['document_name']} - {h['section_ref']} {h['title']} "
        f"(authenticity={h['authenticity']}, jurisdiction={h['jurisdiction']}, version={h['version']})\n{h['text']}"
        for h in passages.values())
    data = wrap_untrusted("requirements", req_text) + "\n\n" + wrap_untrusted("knowledge_base_passages", passage_text)
    return llm.structured("compliance mapping", _INSTRUCTIONS, data, ComplianceOutput, ctx["llm_log"]), passages


def detect_gaps(ctx: dict) -> list[dict]:
    covered = {t for req in ctx["requirements"] for t in detect_topics(req["statement"])}
    gaps = []
    for topic in expected_topics(ctx["project"]["domain"]):
        if topic in covered:
            continue
        label = TOPICS[topic]["label"]
        sources = [h for h in ctx["topic_evidence"].get(topic, [])][:3]
        cited = "; ".join(f"{h['document_name']} {h['section_ref']} ({h['authenticity']})" for h in sources)
        gaps.append(finding(
            "compliance_gap", topic, "High" if topic in _CORE_TOPICS else "Medium",
            f"No requirement addresses: {label}", [],
            explanation=f"Applications in the {ctx['project']['domain'].replace('_', ' ')} domain are normally "
                        f"expected to address {label.lower()}, but none of the submitted requirements does."
                        + ("" if sources else " No supporting passage exists in the knowledge base - "
                                              "this expectation requires verification by a compliance officer."),
            evidence=cited, suggestion=f"Add explicit requirement(s) for {label.lower()} and confirm the "
                                       "applicable obligations with the compliance team.",
            question=f"Which {label.lower()} obligations apply to this application?"))
        # A clarification about this topic addresses the gap only partially (see clarification_context).
        resolve_topic_gap(ctx, gaps[-1], {topic}, TOPICS[topic]["keywords"])
    return gaps


def run(ctx: dict) -> tuple[str, str]:
    db, project = ctx["db"], ctx["project"]
    # Stage 2 regeneration may limit mapping to changed requirements; other mappings are retained.
    only = ctx.get("only_requirement_ids")
    requirements = [r for r in ctx["requirements"] if only is None or r["req_id"] in only]
    retained = [m for m in ctx.get("mappings", []) if only is not None and m["requirement_id"] not in only
                and m["requirement_id"] in ctx["requirement_ids"]]
    # Retrieval uses the requirement plus any clarification linked to it.
    queries = [mask_sensitive(supplementary_text(r))[0] for r in requirements]
    hits_per_req, method = rag.retrieve_many(db, queries, project["jurisdiction"], k=4) if queries else ([], None)
    # Evidence for each expected topic (used to cite sources for gaps).
    topics = expected_topics(project["domain"])
    topic_hits, topic_method = rag.retrieve_many(db, [TOPICS[t]["label"] for t in topics], project["jurisdiction"], k=3)
    ctx["topic_evidence"] = {t: [h for h in hits if t in h["topics"]] for t, hits in zip(topics, topic_hits)}
    method = method or topic_method  # a scoped Stage 2 run may have no requirement to map
    ctx["retrieval_method"] = method

    if method == "none":
        ctx["findings"].append(finding(
            "expert_review", "knowledge_base_unavailable", "High", "Regulatory knowledge base not available",
            [], explanation="No allowlisted knowledge-base documents exist for this jurisdiction, so no "
                            "evidence-based compliance mapping was possible. Coverage is limited.",
            suggestion="Upload official regulatory documents in the Knowledge Base and re-run the analysis."))
        ctx["findings"].extend(detect_gaps(ctx))
        return "partial", "Knowledge base empty: no evidence-based mappings; only topic gaps were assessed."

    mappings: list[dict] = []
    status, note = "completed", ""
    if llm.enabled():
        by_id = {r["req_id"]: r for r in requirements}
        batches = llm.batched(list(zip(requirements, hits_per_req)), 12)
        results = llm.map_parallel(lambda b: _map_batch_llm(b, ctx), batches)
        failed = 0
        for batch, result in zip(batches, results):
            if isinstance(result, Exception):
                failed += 1
                mappings += [m for req, hits in batch for m in map_with_rules(req, hits)]
                continue
            output, passages = result
            for item in output.mappings:
                hit = passages.get(item.chunk_id)
                if not check_reference(ctx, "compliance", hit is not None) or \
                        not valid_ids(ctx, "compliance", [item.requirement_id]):
                    continue
                verified = check_reference(ctx, "compliance", rag.quote_in_text(item.evidence_quote, hit["text"]))
                req = by_id[item.requirement_id]
                evidence = item.evidence_quote if verified else rag.best_sentence(hit["text"], req["statement"])
                rationale = item.rationale if verified else (
                    item.rationale + " [Quoted evidence could not be found in the source; closest passage shown "
                                     "- requires verification.]")
                mappings.append(_mapping(req, hit, evidence, verified, max(0.0, min(1.0, item.confidence)),
                                         rationale, llm.model_label()))
            for gap in output.gaps:
                chunk_ids = [c for c in gap.supporting_chunk_ids if check_reference(ctx, "compliance", c in passages)]
                cited = "; ".join(f"{passages[c]['document_name']} {passages[c]['section_ref']} "
                                  f"({passages[c]['authenticity']})" for c in chunk_ids)
                ctx["findings"].append(finding(
                    "compliance_gap", "llm_identified", "Medium", gap.title, valid_ids(ctx, "compliance",
                                                                                     gap.requirement_ids),
                    explanation=gap.explanation + ("" if chunk_ids else " (No supporting passage - requires "
                                                                        "verification.)"),
                    evidence=cited, suggestion="Confirm with the compliance officer and add requirements if needed.",
                    generated_by=llm.model_label()))
        if failed:
            status, note = "partial", f" OpenAI failed for {failed}/{len(batches)} batches; rule mapping used."
    else:
        mappings = [m for req, hits in zip(requirements, hits_per_req) for m in map_with_rules(req, hits)]

    mappings = retained + mappings
    ctx["mappings"] = mappings
    ctx["findings"].extend(detect_gaps(ctx))
    used = {}
    for m in mappings:
        entry = used.setdefault(m["document_id"], {
            "document_id": m["document_id"], "name": m["document_name"], "doc_type": m["doc_type"],
            "authenticity": m["authenticity"], "jurisdiction": m["jurisdiction"], "version": m["version"],
            "mapped_requirements": set()})
        entry["mapped_requirements"].add(m["requirement_id"])
    ctx["applicable_sources"] = [{**e, "mapped_requirements": sorted(e["mapped_requirements"])} for e in used.values()]
    mapped = len({m["requirement_id"] for m in mappings})
    return status, (f"Created {len(mappings)} control mappings for {mapped} requirements using {method} "
                    f"retrieval across {len(used)} knowledge-base documents.{note}")
