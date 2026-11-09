"""离线 Codex app-server 桩：只用于协议测试，不调用模型。

用法：
    python codex_appserver_stub.py <scenario>
环境变量：
    GUILD_STUB_LOG  可选，JSONL 请求/响应日志路径
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time

SCENARIO = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("GUILD_STUB_SCENARIO", "completed")
LOG_PATH = os.environ.get("GUILD_STUB_LOG", "")

_lock = threading.RLock()
_state = {
    "thread": "th-1",
    "turn": "turn-1",
    "responses": {},
}


def log(direction: str, frame: dict) -> None:
    if not LOG_PATH:
        return
    with _lock:
        with open(LOG_PATH, "a", encoding="utf-8") as handle:
            handle.write(json.dumps({"direction": direction, "frame": frame}) + "\n")


def send(frame: dict) -> None:
    with _lock:
        sys.stdout.write(json.dumps(frame) + "\n")
        sys.stdout.flush()
    log("out", frame)


def respond(mid: object, result: dict) -> None:
    send({"jsonrpc": "2.0", "id": mid, "result": result})


def respond_error(mid: object, code: int, message: str) -> None:
    send({"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}})


def turn_payload(turn_id: str | None = None, status: str = "inProgress") -> dict:
    return {"turn": {"id": turn_id or _state["turn"], "status": status, "items": []}}


def complete(status: str = "completed", thread: str | None = None,
             turn: str | None = None, error: str | None = None) -> None:
    turn_obj: dict = {"id": turn or _state["turn"], "status": status, "items": []}
    if error:
        turn_obj["error"] = {"message": error}
    send({"jsonrpc": "2.0", "method": "turn/completed", "params": {
        "threadId": thread or _state["thread"], "turn": turn_obj}})


def wait_response(mid: object, timeout: float = 3.0) -> dict | None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with _lock:
            if mid in _state["responses"]:
                return _state["responses"][mid]
        time.sleep(0.01)
    return None


def scenario_worker() -> None:
    if SCENARIO == "completed":
        send({"jsonrpc": "2.0", "method": "turn/started", "params": {
            "threadId": _state["thread"], "turn": {"id": _state["turn"], "status": "inProgress", "items": []}}})
        time.sleep(0.02)
        complete("completed")
    elif SCENARIO == "failed":
        time.sleep(0.02)
        complete("failed", error="stub turn failed")
    elif SCENARIO == "interrupted":
        time.sleep(0.02)
        complete("interrupted", error="stub turn interrupted")
    elif SCENARIO == "invalid_status":
        time.sleep(0.02)
        complete("inProgress")
    elif SCENARIO == "wrong_thread":
        time.sleep(0.02)
        complete("completed", thread="th-other")
    elif SCENARIO == "wrong_turn_then_correct":
        time.sleep(0.02)
        complete("completed", turn="turn-other")
        time.sleep(0.08)
        complete("completed")
    elif SCENARIO == "late_completion":
        time.sleep(0.25)
        complete("completed")
    elif SCENARIO == "exit_early":
        time.sleep(0.02)
        os._exit(0)
    elif SCENARIO == "approval":
        send({"jsonrpc": "2.0", "id": 900,
              "method": "item/commandExecution/requestApproval",
              "params": {"threadId": _state["thread"], "turnId": _state["turn"],
                         "itemId": "item-1", "startedAtMs": int(time.time() * 1000),
                         "command": "echo unsafe"}})
        wait_response(900)
        complete("completed")
    elif SCENARIO == "unsupported":
        send({"jsonrpc": "2.0", "id": 901, "method": "item/tool/call",
              "params": {"threadId": _state["thread"], "turnId": _state["turn"]}})
        wait_response(901)
        complete("completed")
    elif SCENARIO == "notification_only":
        send({"jsonrpc": "2.0", "method": "warning", "params": {"message": "stub warning"}})
        complete("completed")
    elif SCENARIO == "timeout":
        pass
    elif SCENARIO == "interrupt":
        pass
    else:
        time.sleep(0.02)
        complete("completed")


def handle_turn_start(mid: object) -> None:
    if SCENARIO == "stall_method":
        return
    if SCENARIO == "turn_start_error":
        respond_error(mid, -32000, "turn start rejected")
        return
    if SCENARIO == "turn_start_failed_response":
        respond(mid, {"turn": {"id": _state["turn"], "status": "failed", "items": [],
                               "error": {"message": "immediate failure"}}})
        return
    if SCENARIO == "completed_before_response":
        complete("completed")
        time.sleep(0.3)
        respond(mid, turn_payload())
        return
    respond(mid, turn_payload())
    threading.Thread(target=scenario_worker, daemon=True).start()


def main() -> int:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            frame = json.loads(line)
        except json.JSONDecodeError:
            continue
        log("in", frame)
        method = frame.get("method")
        mid = frame.get("id")
        if method is None:
            with _lock:
                _state["responses"][mid] = frame
            continue
        if method == "initialize":
            respond(mid, {"serverInfo": {"name": "stub", "version": "0"}})
        elif method == "thread/start":
            respond(mid, {"thread": {"id": _state["thread"]}})
        elif method == "turn/start":
            handle_turn_start(mid)
        elif method == "turn/interrupt":
            respond(mid, {})
            if SCENARIO in ("interrupt", "cancel"):
                complete("interrupted")
        else:
            respond_error(mid, -32601, f"stub does not implement {method}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
