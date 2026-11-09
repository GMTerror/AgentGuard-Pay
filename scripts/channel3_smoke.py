"""Check the Channel3 integration with your key.

Put CHANNEL3_API_KEY in a .env file, or export it.
python -m scripts.channel3_smoke "coffee mug"
"""

import os
import sys

from dotenv import load_dotenv

from agentguard.catalog import Channel3Catalog
from agentguard.risk import turn_score

load_dotenv()

query = " ".join(sys.argv[1:]) or "coffee mug"
cat = Channel3Catalog(os.environ["CHANNEL3_API_KEY"])
items = cat.search(query)
print(f"{len(items)} purchasable offer(s) for {query!r}\n")
for it in items[:10]:
    print(
        f"{it['item_id']:<32} {it['merchant']:<22} {it['price']:>8} {it['currency']}  {it['name'][:50]}"
    )
if items:
    first = items[0]
    print("\nSnapshot the gate would use:", cat.get(first["item_id"]) is not None)
    print(
        "Injection score of first description:",
        turn_score(first["name"] + " " + first["description"]),
    )
