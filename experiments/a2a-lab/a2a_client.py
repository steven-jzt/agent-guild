"""一个极简的 A2A 客户端封装：发现 Agent Card -> SendMessage -> 取 artifact。"""

from __future__ import annotations

import httpx

from _paths import LAB  # noqa: F401  （确保 vendor 在 sys.path 上）

from a2a.client import A2ACardResolver, ClientConfig, create_client
from a2a.helpers import get_data_parts, new_data_message
from a2a.types import Role, SendMessageRequest, TaskState


class AgentCallError(RuntimeError):
    pass


async def fetch_card(base_url: str):
    async with httpx.AsyncClient(timeout=15, trust_env=False) as http:
        resolver = A2ACardResolver(httpx_client=http, base_url=base_url)
        return await resolver.get_agent_card()


async def call_agent(base_url: str, payload: dict, timeout: float = 30.0) -> dict:
    """向一个 A2A agent 发送结构化任务，返回它 artifact 里的 data。"""
    async with httpx.AsyncClient(timeout=timeout, trust_env=False) as http:
        resolver = A2ACardResolver(httpx_client=http, base_url=base_url)
        card = await resolver.get_agent_card()
        client = await create_client(
            agent=card, client_config=ClientConfig(streaming=False)
        )
        try:
            request = SendMessageRequest(
                message=new_data_message(payload, role=Role.ROLE_USER)
            )
            task = None
            async for chunk in client.send_message(request):
                if chunk.HasField("task"):
                    task = chunk.task
                elif chunk.HasField("artifact_update") and task is not None:
                    task.artifacts.append(chunk.artifact_update.artifact)
                elif chunk.HasField("status_update") and task is not None:
                    task.status.CopyFrom(chunk.status_update.status)
        finally:
            await client.close()

    if task is None:
        raise AgentCallError(f"{base_url} 没有返回 Task")
    if task.status.state != TaskState.TASK_STATE_COMPLETED:
        message = ""
        if task.status.HasField("message"):
            message = " ".join(p.text for p in task.status.message.parts if p.HasField("text"))
        raise AgentCallError(
            f"{base_url} 任务未完成：state={task.status.state} message={message!r}"
        )
    for artifact in task.artifacts:
        data = get_data_parts(artifact.parts)
        if data:
            return data[-1]
    raise AgentCallError(f"{base_url} 的任务没有 data artifact")
