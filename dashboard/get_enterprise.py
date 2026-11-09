"""Download AG Grid Enterprise for YOUR machine (it is commercial software and is not in this repo).

  python -m dashboard.get_enterprise
  # then put AG_GRID_LICENSE_KEY=... in .env and restart: python -m dashboard.server

Fetches the pinned version from the npm registry, checks its published integrity hash, and saves one
file to dashboard/static/vendor-enterprise/ (git-ignored). The version must match the bundled community
grid, so it is pinned here.
"""

import base64
import hashlib
import io
import json
import os
import sys
import tarfile
import urllib.request

VERSION = "32.3.9"
MEMBER = "package/dist/ag-grid-enterprise.min.noStyle.js"
DEST = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "static", "vendor-enterprise"
)


def verify(integrity: str, data: bytes) -> bool:
    algo, _, expected = integrity.partition("-")
    if algo != "sha512":
        return False
    return base64.b64encode(hashlib.sha512(data).digest()).decode() == expected


def extract(tgz: bytes, dest_dir: str) -> str:
    with tarfile.open(fileobj=io.BytesIO(tgz), mode="r:gz") as t:
        f = t.extractfile(MEMBER)
        if f is None:
            raise ValueError(f"{MEMBER} not found in the package")
        os.makedirs(dest_dir, exist_ok=True)
        path = os.path.join(dest_dir, os.path.basename(MEMBER))
        with open(path, "wb") as out:
            out.write(f.read())
    return path


def main():
    meta_url = f"https://registry.npmjs.org/ag-grid-enterprise/{VERSION}"
    with urllib.request.urlopen(meta_url, timeout=30) as r:
        meta = json.load(r)
    print(f"Downloading AG Grid Enterprise {VERSION} ...")
    with urllib.request.urlopen(meta["dist"]["tarball"], timeout=120) as r:
        data = r.read()
    if not verify(meta["dist"]["integrity"], data):
        sys.exit(
            "Integrity check failed: the download does not match the registry hash. Not saved."
        )
    print("Saved:", extract(data, DEST))
    print(
        "Add AG_GRID_LICENSE_KEY=<your trial key> to .env, then restart the dashboard server."
    )


if __name__ == "__main__":
    main()
