"""Attack 2: stolen / replayed / forged spending mandates.
Run: python -m demo.attacks.replay_mandate"""

import copy
import os
import time
import uuid

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from agentguard.audit import AuditLog, verify_log
from agentguard.mandate import Mandate, MandateError, MandateVerifier, sign_mandate
from demo.common import OUT_DIR, banner


def main():
    banner("ATTACK 2: replayed, tampered and forged mandates")
    now = int(time.time())
    alice, attacker = Ed25519PrivateKey.generate(), Ed25519PrivateKey.generate()
    verifier = MandateVerifier({"alice": alice.public_key()})
    os.makedirs(OUT_DIR, exist_ok=True)
    path = f"{OUT_DIR}/replay_mandate.jsonl"
    if os.path.exists(path):
        os.remove(path)
    audit = AuditLog(path)

    def mk(**kw):
        base = {
            "user_id": "alice",
            "session_id": "s-100",
            "nonce": uuid.uuid4().hex,
            "issued_at": now,
            "expires_at": now + 600,
            "max_total": "50.00",
            "currency": "USD",
            "allowed_merchants": ("shop-a",),
        }
        return Mandate(**{**base, **kw})

    legit = sign_mandate(mk(), alice)
    tampered = copy.deepcopy(legit)
    tampered["mandate"]["max_total"] = "5000.00"

    print("\nWithout signing: a plain JSON limit field.")
    plain = {"user_id": "alice", "max_total": "50.00"}
    plain["max_total"] = "5000.00"
    print(f"  [ACCEPTED] limit is now ${plain['max_total']}. Nothing detects the edit.")

    print("\nWith AgentGuard (Ed25519-signed, single-use, session-bound, expiring):")
    attempts = [
        ("Legit session starts", lambda: verifier.verify(legit, "s-100")),
        ("Attacker replays the same mandate", lambda: verifier.verify(legit, "s-100")),
        (
            "Attacker replays it in another session",
            lambda: verifier.verify(legit, "s-777"),
        ),
        (
            "Attacker edits limit $50 -> $5000",
            lambda: verifier.verify(tampered, "s-100"),
        ),
        (
            "Attacker signs a mandate as 'alice'",
            lambda: verifier.verify(
                sign_mandate(mk(session_id="s-300"), attacker), "s-300"
            ),
        ),
        (
            "Stolen mandate that already expired",
            lambda: verifier.verify(
                sign_mandate(
                    mk(session_id="s-400", issued_at=now - 3600, expires_at=now - 10),
                    alice,
                ),
                "s-400",
            ),
        ),
        (
            "Mandate for an unregistered user",
            lambda: verifier.verify(
                sign_mandate(mk(user_id="mallory", session_id="s-500"), attacker),
                "s-500",
            ),
        ),
    ]
    outcomes = []
    for label, attempt in attempts:
        try:
            attempt()
            outcomes.append((label, True, "accepted"))
            audit.log("mandate_accepted", attempt=label)
            print(f"  [OK]      {label}")
        except MandateError as e:
            outcomes.append((label, False, str(e)))
            audit.log("mandate_rejected", attempt=label, reason=str(e))
            print(f"  [BLOCKED] {label}: {e}")

    accepted = sum(1 for _, ok, _ in outcomes if ok)
    ok = verify_log(path)
    print(
        f"\n  RESULT: {accepted} of {len(outcomes)} accepted (only the legitimate one). "
        f"Audit chain intact: {ok}"
    )
    return {"outcomes": outcomes, "accepted": accepted, "audit_ok": ok}


if __name__ == "__main__":
    main()
