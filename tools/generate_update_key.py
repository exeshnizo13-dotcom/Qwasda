"""Generate an Ed25519 updater keypair without overwriting existing files."""

from __future__ import annotations

import argparse
import base64
import sys
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--key-id", required=True)
    parser.add_argument("--private-output", type=Path, required=True)
    args = parser.parse_args()
    if args.private_output.exists():
        raise SystemExit(f"Refusing to overwrite {args.private_output}")
    private = Ed25519PrivateKey.generate()
    seed = base64.b64encode(private.private_bytes_raw()).decode("ascii")
    public = base64.b64encode(private.public_key().public_bytes_raw()).decode("ascii")
    args.private_output.parent.mkdir(parents=True, exist_ok=True)
    args.private_output.write_text(seed + "\n", encoding="ascii")
    sys.stdout.write(f"QWASDA_UPDATE_KEY_ID={args.key_id}\n")
    sys.stdout.write(f"QWASDA_UPDATE_PUBLIC_KEYS={args.key_id}:{public}\n")
    sys.stdout.write("Store the private seed as GitHub secret QWASDA_UPDATE_SIGNING_KEY.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
