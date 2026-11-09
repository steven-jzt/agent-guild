"""Audit Agent：一个会“调用别的 agent”的 A2A server。

收到审计任务后，它自己作为 A2A 客户端去调用 Ledger Agent，拿到报表再核对。
这条 audit -> ledger 的调用不经过任何中心编排者，是真正的 agent-to-agent。
"""

from __future__ import annotations

import os

import uvicorn
from starlette.applications import Starlette

from _paths import LAB  # noqa: F401
from a2a_client import call_agent
from a2a.helpers import (
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

LEDGER_URL = os.environ.get("LEDGER_URL", "http://127.0.0.1:8765")
PROTOCOL_BINDING = "JSONRPC"


def build_card(port: int) -> AgentCard:
    return AgentCard(
        name="Audit Agent",
        description="账目审计 agent：通过 A2A 调用 Ledger Agent，核对金额与分类汇总。",
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
                id="audit_ledger",
                name="Audit ledger",
                description="向 Ledger Agent 索取报表并做一致性检查。",
                tags=["audit", "ledger"],
                examples=['{"action":"audit"}'],
                input_modes=["application/json"],
                output_modes=["application/json"],
            )
        ],
    )


class AuditExecutor(AgentExecutor):
    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        if task is None:
            task = new_task_from_user_message(context.message)
            await event_queue.enqueue_event(task)

        updater = TaskUpdater(event_queue, task.id, task.context_id)
        await updater.update_status(
            TaskState.TASK_STATE_WORKING, new_text_message("正在向 Ledger Agent 取证")
        )

        try:
            ledger = await call_agent(LEDGER_URL, {"action": "report"})
            summary = ledger.get("summary", {}) or {}
            by_category = summary.get("by_category", {}) or {}
            total = float(summary.get("total", 0.0))
            count = int(summary.get("count", 0))
            category_sum = round(sum(float(v) for v in by_category.values()), 2)
            text = ledger.get("text", "")

            checks = [
                {
                    "name": "total_equals_category_sum",
                    "ok": abs(total - category_sum) < 0.011,
                    "detail": f"total={total:.2f} category_sum={category_sum:.2f}",
                },
                {
                    "name": "count_is_non_negative",
                    "ok": count >= 0,
                    "detail": f"count={count}",
                },
                {
                    "name": "report_text_contains_total",
                    "ok": f"合计 {total:.2f}" in text,
                    "detail": f"expect 合计 {total:.2f}",
                },
            ]
            payload = {
                "ok": all(c["ok"] for c in checks),
                "checks": checks,
                "ledger": summary,
                "report_text": text,
                "source": LEDGER_URL,
            }
            await updater.add_artifact(parts=[new_data_part(payload)], name="audit")
            await updater.complete(new_text_message("审计完成"))

        except Exception as exc:  # noqa: BLE001
            await updater.failed(new_text_message(f"审计失败: {exc}"))

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        if context.current_task is not None:
            updater = TaskUpdater(event_queue, context.current_task.id, context.current_task.context_id)
            await updater.cancel(new_text_message("已取消"))


def main() -> None:
    port = int(os.environ.get("A2A_PORT", "8766"))
    card = build_card(port)
    handler = DefaultRequestHandler(
        agent_executor=AuditExecutor(),
        task_store=InMemoryTaskStore(),
        agent_card=card,
    )
    routes = create_agent_card_routes(card) + create_jsonrpc_routes(handler, "/")
    app = Starlette(routes=routes)
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning", access_log=False)


if __name__ == "__main__":
    main()
