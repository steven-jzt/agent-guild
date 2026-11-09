"""Ledger Agent：把 demo-ledger 的记账能力包装成一个 A2A server。

启动：python ledger_agent.py   （默认 127.0.0.1:8765）
协议：A2A v1.0 / JSON-RPC binding，Agent Card 在 /.well-known/agent-card.json
"""

from __future__ import annotations

import os

import uvicorn
from starlette.applications import Starlette

from _paths import LAB  # noqa: F401

import report as ledger_report
import storage as ledger_storage
from a2a.helpers import (
    get_data_parts,
    new_data_part,
    new_task_from_user_message,
    new_text_message,
)
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    AgentSkill,
    TaskState,
)

PROTOCOL_BINDING = "JSONRPC"


def build_card(port: int) -> AgentCard:
    return AgentCard(
        name="Ledger Agent",
        description="个人记账 agent：新增条目、列出账目、生成汇总报表。",
        version="1.0.0",
        default_input_modes=["application/json"],
        default_output_modes=["application/json"],
        capabilities=AgentCapabilities(streaming=False, push_notifications=False),
        supported_interfaces=[
            AgentInterface(
                protocol_binding=PROTOCOL_BINDING,
                url=f"http://127.0.0.1:{port}",
                protocol_version="1.0",
            )
        ],
        skills=[
            AgentSkill(
                id="ledger",
                name="Ledger operations",
                description="action=add / list / report；add 需要 amount、category，可选 note、date。",
                tags=["ledger", "finance", "json"],
                examples=['{"action":"add","amount":12.5,"category":"餐饮"}', '{"action":"report"}'],
                input_modes=["application/json"],
                output_modes=["application/json"],
            )
        ],
    )


def _first_data(context: RequestContext) -> dict:
    if context.message is None:
        return {}
    data = get_data_parts(context.message.parts)
    return data[0] if data and isinstance(data[0], dict) else {}


class LedgerExecutor(AgentExecutor):
    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        if task is None:
            task = new_task_from_user_message(context.message)
            await event_queue.enqueue_event(task)

        updater = TaskUpdater(event_queue, task.id, task.context_id)
        await updater.update_status(
            TaskState.TASK_STATE_WORKING, new_text_message("处理账本请求")
        )

        try:
            request = _first_data(context)
            action = request.get("action")

            if action == "add":
                entry = {
                    "amount": request.get("amount"),
                    "category": request.get("category"),
                    "note": request.get("note", ""),
                }
                if request.get("date"):
                    entry["date"] = request["date"]
                payload = {"ok": True, "entry": ledger_storage.append_entry(entry)}

            elif action == "list":
                entries = ledger_storage.load_entries()
                limit = request.get("limit")
                if isinstance(limit, (int, float)) and limit > 0:
                    entries = entries[-int(limit):]
                payload = {"ok": True, "entries": entries, "count": len(entries)}

            elif action == "report":
                entries = ledger_storage.load_entries()
                summary = ledger_report.summarize(entries)
                payload = {
                    "ok": True,
                    "summary": summary,
                    "text": ledger_report.format_report(summary),
                }

            else:
                raise ValueError(f"未知 action: {action!r}")

            await updater.add_artifact(parts=[new_data_part(payload)], name="result")
            await updater.complete(new_text_message("完成"))

        except Exception as exc:  # noqa: BLE001
            await updater.failed(new_text_message(f"错误: {exc}"))

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        if context.current_task is not None:
            updater = TaskUpdater(event_queue, context.current_task.id, context.current_task.context_id)
            await updater.cancel(new_text_message("已取消"))


def main() -> None:
    port = int(os.environ.get("A2A_PORT", "8765"))
    os.environ.setdefault(
        "LEDGER_DATA", str(LAB.parent / "demo-ledger" / ".tmp" / "a2a-ledger.jsonl")
    )
    card = build_card(port)
    handler = DefaultRequestHandler(
        agent_executor=LedgerExecutor(),
        task_store=InMemoryTaskStore(),
        agent_card=card,
    )
    routes = create_agent_card_routes(card) + create_jsonrpc_routes(handler, "/")
    app = Starlette(routes=routes)
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning", access_log=False)


if __name__ == "__main__":
    main()
