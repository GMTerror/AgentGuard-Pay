"""Tune the semantic check with the real model.
python -m scripts.semantic_probe "buy me a mug" "Ceramic coffee mug" "Premium wellness bundle"
"""

import sys

from agentguard.semantic import IntentTracker, MiniLM

if len(sys.argv) < 3:
    sys.exit(__doc__)
t = IntentTracker(MiniLM())
t.add(sys.argv[1])
for c in sys.argv[2:]:
    print(f"similarity {t.similarity(c):5.2f}   drift {t.drift(c):4.2f}   {c}")
