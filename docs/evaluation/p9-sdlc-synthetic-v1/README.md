# E9 - SDLC recommendation agreement (P9-SDLC-SYNTHETIC-v1)

**Benchmark review status: PROJECT-AUTHOR REVIEW COMPLETED** (recorded 2026-09-26, *after* the run
below). The project author reviewed the benchmark materials and identified no substantive
corrections, so v1 stays frozen. The benchmark remains synthetic: its cases and its panel are
AI-generated, it is not independently validated or expert-validated, and no real expert reviewed
it. Nothing below was changed or re-run.

**Approved deviation P9-3** (2026-09-27): the project author explicitly approved using this
synthetic, AI-generated panel in place of the blind expert panel that Phase 0 O.1 and the P9 exit
criterion name. It closes the roadmap criterion only. The panel is still **not a real or
independently validated expert panel**, and E9 below measures agreement with it and nothing more.
See `docs/12` §15-§17.

> **E9 measures agreement between ReqPilot and a synthetic AI-generated expert-panel simulation. It is an engineering benchmark, not independent human-expert validation.**

- Benchmark: `P9-SDLC-SYNTHETIC-v1`, manifest sha256 `3486a56fcb6cb44aaca340f27680e5b172d417b7e874a071db3a900922a753d5` (verified before the run).
- Validation status: synthetic; the panel is AI-generated personas, not real experts; not independently validated; not expert-validated; an engineering benchmark only.
- System under test: `sdlc_rules@1.0.0#41730a927957` (sha256 `41730a927957ecabd369f2ee6dc3aca3da242b1e5201d83a7fb6685643340904`); matches the ruleset frozen with the benchmark: **True**. deterministic: P7 matrix + I.6 aggregates, P9 derivation, MCDA, rule pass; no model call.
- Evaluated at 2026-09-25T21:14:13+00:00 (the run is deterministic; only this timestamp changes between runs).
- Target: none. No approved numeric target exists for E9 (Phase 0 O.1 says targets for E2-E9 are set from measured behaviour, not guessed). This is a first measurement against a synthetic AI-generated panel - not a threshold that was met, and not human-expert validation.

## Summary

| Measure | Value |
|---|---|
| Cases | 12 |
| **E9 top-choice agreement** (ReqPilot first = panel Borda first) | 2/12 (16.67%) |
| ReqPilot first within the panel's top 2 | 5/12 (41.67%) |
| Panel first within ReqPilot's top 2 | 3/12 (25.00%) |
| ReqPilot first = a panel plurality choice | 2/12 (16.67%) |
| ReqPilot first preferred by at least one panellist | 4/12 |
| Mean Kendall's tau (full ranking) | 0.4365 |
| Mean Spearman's rho (full ranking) | 0.5327 |
| Mean pairwise order agreement | 0.7182 |
| Mean panel consensus strength (share of panellists whose first = Borda first) | 0.8 |
| Mean panel Kendall's W (inter-panellist concordance) | 0.9031 |

First choices - ReqPilot: `{'devsecops': 1, 'agile_v_model_hybrid': 10, 'agile': 1}`; panel: `{'agile_devsecops_hybrid': 2, 'v_model': 6, 'agile': 1, 'agile_v_model_hybrid': 1, 'spiral': 2}`.

## Per case

| Case | ReqPilot first | Panel first (Borda) | Panel plurality | Consensus | tau | pairwise | Rules triggered |
|---|---|---|---|---|---|---|---|
| C01 | `devsecops` | `agile_devsecops_hybrid` | `agile_devsecops_hybrid` | 0.8 | 0.4286 | 0.7143 | R3-boost-devsecops-security, R2-require-v-model-formal-critical |
| C02 | `agile_v_model_hybrid` | `v_model` | `v_model` | 0.8 | 0.2381 | 0.619 | R4-boost-hybrid-regulated-evolving, R1-veto-waterfall-regulated-unstable, R2-require-v-model-formal-critical |
| C03 | `agile` | `agile` | `agile` | 0.8 | 1.0 | 1.0 | - |
| C04 | `agile_v_model_hybrid` | `v_model` | `v_model` | 1.0 | 0.4286 | 0.7143 | R4-boost-hybrid-regulated-evolving, R1-veto-waterfall-regulated-unstable, R2-require-v-model-formal-critical |
| C05 | `agile_v_model_hybrid` | `agile_v_model_hybrid` | `agile_v_model_hybrid` | 0.6 | 0.619 | 0.8095 | R3-boost-devsecops-security, R4-boost-hybrid-regulated-evolving, R1-veto-waterfall-regulated-unstable, R2-require-v-model-formal-critical |
| C06 | `agile_v_model_hybrid` | `v_model` | `v_model` | 1.0 | 0.1429 | 0.5714 | - |
| C07 | `agile_v_model_hybrid` | `agile_devsecops_hybrid` | `agile_devsecops_hybrid` | 0.8 | 0.4286 | 0.7143 | - |
| C08 | `agile_v_model_hybrid` | `v_model` | `v_model` | 1.0 | -0.0476 | 0.4762 | R4-boost-hybrid-regulated-evolving, R1-veto-waterfall-regulated-unstable, R2-require-v-model-formal-critical |
| C09 | `agile_v_model_hybrid` | `spiral` | `spiral` | 0.6 | 0.619 | 0.8095 | - |
| C10 | `agile_v_model_hybrid` | `v_model` | `v_model` | 0.6 | 0.2381 | 0.619 | R4-boost-hybrid-regulated-evolving, R1-veto-waterfall-regulated-unstable, R2-require-v-model-formal-critical |
| C11 | `agile_v_model_hybrid` | `v_model` | `v_model` | 1.0 | 0.7143 | 0.8571 | R2-require-v-model-formal-critical |
| C12 | `agile_v_model_hybrid` | `spiral` | `spiral` | 0.6 | 0.4286 | 0.7143 | R4-boost-hybrid-regulated-evolving, R1-veto-waterfall-regulated-unstable, R2-require-v-model-formal-critical |

## Disagreements

Cases where ReqPilot's first choice differs from the panel's Borda winner: C01, C02, C04, C06, C07, C08, C09, C10, C11, C12. They are reported as measured; nothing was tuned after this comparison, and any correction to the scoring or the benchmark belongs in a new version (v2).

## Limitations

- The panel is five AI-generated personas answering independently and blind to ReqPilot's output; it is a simulation of expert judgement, not experts.
- Twelve synthetic cases; the numbers describe this benchmark only.
- The comparison exercises the deterministic path (derivation, MCDA, rules). Model proposals and human overrides - which can move a real recommendation - are not part of it.
