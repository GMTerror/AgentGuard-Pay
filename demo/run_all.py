"""Run all three attack demos. Usage: python -m demo.run_all"""

from demo.attacks import (
    malicious_catalog,
    multi_turn_drift,
    replay_mandate,
    semantic_drift,
)

for module in (malicious_catalog, replay_mandate, multi_turn_drift, semantic_drift):
    module.main()
