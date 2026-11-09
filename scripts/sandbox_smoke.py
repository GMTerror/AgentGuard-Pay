"""Milestone 1: token -> create order -> buyer approves -> capture. Run this first.

Put PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET (sandbox app) in a .env file, or export them.
python -m scripts.sandbox_smoke
"""

import os
import uuid
from decimal import Decimal

from dotenv import load_dotenv

from agentguard.paypal_client import PayPalClient

load_dotenv()

c = PayPalClient(os.environ["PAYPAL_CLIENT_ID"], os.environ["PAYPAL_CLIENT_SECRET"])
order = c.create_order(
    Decimal("10.00"),
    "USD",
    request_id=str(uuid.uuid4()),
    return_url="https://example.com/return",
    cancel_url="https://example.com/cancel",
)
print("Order:", order["id"], order["status"])
print(
    "Open this URL and approve as your SANDBOX BUYER account:\n", order["approve_url"]
)
input("Press Enter after approving (you'll land on example.com, that's fine)... ")

info = c.get_order(order["id"])
print("After approval:", info)
if info["status"] != "APPROVED":
    raise SystemExit(f"Not approved (status={info['status']}), not capturing")
print("Captured:", c.capture_order(order["id"], request_id=str(uuid.uuid4())))
