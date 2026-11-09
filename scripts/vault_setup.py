"""One-time setup for auto-pay: save the sandbox buyer's PayPal.

  python -m scripts.vault_setup [--amount 1.00]

Charges a small sandbox amount (this is how PayPal's save-with-purchase flow works), then prints
a payment token. Put it in your .env as PAYPAL_VAULT_TOKEN. Treat it like a password: with your
API credentials it can charge that buyer, so never commit it or paste it anywhere public.
"""

import argparse
import os
import sys
import uuid
from decimal import Decimal

from dotenv import load_dotenv

from agentguard.paypal_client import PayPalClient

load_dotenv()
ap = argparse.ArgumentParser(
    description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
)
ap.add_argument("--amount", default="1.00")
ap.add_argument("--currency", default="USD")
args = ap.parse_args()

c = PayPalClient(os.environ["PAYPAL_CLIENT_ID"], os.environ["PAYPAL_CLIENT_SECRET"])
order = c.create_order(
    Decimal(args.amount),
    args.currency.upper(),
    request_id=str(uuid.uuid4()),
    return_url="https://example.com/return",
    cancel_url="https://example.com/cancel",
    vault=True,
)
print("Order:", order["id"], order["status"])
print(
    "Open this URL, log in as your SANDBOX BUYER, approve, and agree to save PayPal for future payments:\n",
    order["approve_url"],
)
input("Press Enter after approving... ")

info = c.get_order(order["id"])
if info["status"] != "APPROVED":
    sys.exit(f"Not approved (status={info['status']}), not capturing")
res = c.capture_order(order["id"], request_id=str(uuid.uuid4()))
print("Captured:", {k: v for k, v in res.items() if k != "vault_id"})
if not res["vault_id"]:
    sys.exit(
        "PayPal did not return a saved-payment token. Saving payment methods may not be enabled for "
        "your sandbox app; check the app's features in the PayPal developer dashboard, or use the "
        "manual flow (run_agent without --auto)."
    )
print(
    "\nSaved. Add this line to your .env (and keep it secret):\nPAYPAL_VAULT_TOKEN="
    + res["vault_id"]
)
