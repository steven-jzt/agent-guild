"""A2A 演示：客户端 -> Ledger Agent，客户端 -> Audit Agent -> Ledger Agent。

先启动两个 server（见 README），再运行：
    python demo.py
"""

from __future__ import annotations

import asyncio
import json
import os

from _paths import LAB  # noqa: F401
from a2a_client import call_agent, fetch_card

LEDGER_URL = os.environ.get("LEDGER_URL", "http://127.0.0.1:8765")
AUDIT_URL = os.environ.get("AUDIT_URL", "http://127.0.0.1:8766")


def dump(title: str, data) -> None:
    print(f"\n=== {title} ===")
    print(json.dumps(data, ensure_ascii=False, indent=2))


async def main() -> None:
    for url in (LEDGER_URL, AUDIT_URL):
        card = await fetch_card(url)
        print(
            f"[discover] {card.name} v{card.version} "
            f"skills={[s.id for s in card.skills]} "
            f"interface={card.supported_interfaces[0].url} "
            f"binding={card.supported_interfaces[0].protocol_binding} "
            f"protocol={card.supported_interfaces[0].protocol_version}"
        )

    dump("client -> ledger: add 28.5 餐饮", await call_agent(
        LEDGER_URL, {"action": "add", "amount": 28.5, "category": "餐饮", "note": "晚饭"}
    ))
    dump("client -> ledger: add -8000 工资", await call_agent(
        LEDGER_URL, {"action": "add", "amount": -8000, "category": "工资", "note": "十月"}
    ))
    dump("client -> ledger: add 3 交通", await call_agent(
        LEDGER_URL, {"action": "add", "amount": 3, "category": "交通", "note": "地铁"}
    ))

    ledger_report = await call_agent(LEDGER_URL, {"action": "report"})
    dump("client -> ledger: report", ledger_report)

    audit = await call_agent(AUDIT_URL, {"action": "audit"})
    dump("client -> audit -> ledger: audit", audit)

    print("\n=== 结论 ===")
    print(f"audit.ok = {audit['ok']}（audit agent 自己调用了 ledger agent，未经过客户端）")


if __name__ == "__main__":
    asyncio.run(main())
