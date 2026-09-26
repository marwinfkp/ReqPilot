"""SDLC recommendation - the pure, deterministic core of P9 (architecture L, I.6).

Nothing in this package performs I/O, calls a model or reads configuration from
disk. It holds:

* :mod:`~reqpilot.domain.sdlc.factors` - the thirteen ``[PS §13]`` factors and
  the 1-5 scale (``[DESIGN] D11``);
* :mod:`~reqpilot.domain.sdlc.config` - the typed shape of the versioned SDLC
  ruleset (candidates, weights, suitability coefficients, rules, derivation
  bands); the YAML itself is loaded by :mod:`reqpilot.rules.sdlc`;
* :mod:`~reqpilot.domain.sdlc.facts` - the approved project facts a factor
  profile is derived from, each carrying the evidence references behind it;
* :mod:`~reqpilot.domain.sdlc.derivation` - facts -> 13 factor scores;
* :mod:`~reqpilot.domain.sdlc.scoring` - weighted MCDA, the rule pass, the
  ranking and the reversal analysis (architecture L.3, L.4; ``FR-SDL-007``);
* :mod:`~reqpilot.domain.sdlc.consistency` - the explanation consistency check
  (``[DESIGN] D9``).

"The LLM proposes; deterministic code disposes": everything with authority -
a factor score after validation, a weight, a coefficient, a rule, a suitability
score, a rank - is computed here, from data, and is independently testable by
hand.
"""
