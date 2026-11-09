"""Adapter 探测 CLI：python adapter_cli.py probe [--name codex|dsh|mock]。"""

from __future__ import annotations

import argparse
import json

from adapters import all_adapters


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("probe")
    p.add_argument("--name")
    args = ap.parse_args()

    adapters = all_adapters()
    if args.name:
        if args.name not in adapters:
            raise SystemExit(f"unknown adapter: {args.name}")
        result = adapters[args.name].probe()
    else:
        result = [a.probe() for a in adapters.values()]
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
