# Extraction evaluation - E1

- Gold set: `e1_synthetic` v1 (manifest sha256 `6bce917368818c29...`)
- Run: `111b163e-616c-4767-82b1-49c8495127a1`; providers ['openai']; models ['gpt-5.6-luna']; prompts ['requirement_extraction@1.0.0']
- Gold items: 60; predicted items: 60
- Candidate pairs proposed: 58; adjudicated: 58
- Matching rule: semantic match adjudicated by humans; code proposes candidate pairs (span overlap on the same transcript, or token-set Jaccard >= 0.5); a maximum one-to-one matching is taken over pairs adjudicated 'match'
- Matched: 58
- Precision 0.967, recall 0.967, F1 0.967
- ET-07 provisional target: F1 >= 0.75 (to be re-baselined from this measurement; not a pass mark)

## Limitations
- the gold set is produced by the team that built the system (Phase 0 O.3)
- case studies are synthetic (Phase 0 O.3)
- ET-07 is provisional and is re-baselined by this measurement, not tested by it
- model output varies between live runs; recorded replays are byte-identical
