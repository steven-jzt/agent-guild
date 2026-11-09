"""Agent adapter 合同：Codex / DSH / 通用命令 / Mock。

设计原则：
- 适配器只负责启动、停止、探测、事件；不负责验收、合入、释放预约。
- 厂商未提供的能力明确返回 unsupported，不伪造。
- 真实接入需要配置命令/凭据；默认 probe 返回 unavailable。
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable


class AdapterError(RuntimeError):
    pass


class BaseAdapter:
    name = "base"

    def probe(self) -> dict:
        return {"name": self.name, "available": False, "reason": "not configured"}

    def start(self, task_id: str, workspace: str, env: dict | None = None,
              prompt: str | None = None) -> dict:
        raise AdapterError(f"{self.name}: start not implemented")

    def stop(self, run_id: str) -> dict:
        raise AdapterError(f"{self.name}: stop not implemented")

    def events(self, run_id: str) -> list[dict]:
        return []


class CommandAdapter(BaseAdapter):
    """通用 CLI 适配器：用 argv 启动一个子进程。"""

    accept_prompt = False

    def __init__(self, name: str, command: list[str] | None = None):
        self.name = name
        self.command = list(command or [])
        self._procs: dict[str, subprocess.Popen] = {}

    def probe(self) -> dict:
        if not self.command:
            return {"name": self.name, "available": False, "reason": "command not configured"}
        candidate = Path(self.command[0])
        exe = str(candidate) if candidate.is_file() else shutil.which(self.command[0])
        if exe is None:
            return {"name": self.name, "available": False,
                    "reason": f"executable not found: {self.command[0]}"}
        return {
            "name": self.name,
            "available": True,
            "executable": exe,
            "command": self.command,
        }

    def start(self, task_id: str, workspace: str, env: dict | None = None,
              prompt: str | None = None) -> dict:
        info = self.probe()
        if not info["available"]:
            raise AdapterError(info["reason"])
        cwd = Path(workspace).resolve()
        if not cwd.is_dir():
            raise AdapterError(f"workspace not found: {cwd}")
        merged_env = os.environ.copy()
        merged_env.update(env or {})
        cmd = [info["executable"], *self.command[1:]]
        if prompt and self.accept_prompt:
            cmd.append(prompt)
        proc = subprocess.Popen(
            cmd,
            cwd=str(cwd),
            env=merged_env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        run_id = "AR-" + uuid.uuid4().hex[:8]
        self._procs[run_id] = proc
        return {
            "adapter": self.name,
            "run_id": run_id,
            "task_id": task_id,
            "workspace": str(cwd),
            "pid": proc.pid,
            "status": "running",
            "events": "unsupported",
        }

    def stop(self, run_id: str) -> dict:
        proc = self._procs.get(run_id)
        if proc is None:
            raise AdapterError(f"unknown adapter run: {run_id}")
        if proc.poll() is not None:
            self._procs.pop(run_id, None)
            return {"run_id": run_id, "status": "already_exited", "exit_code": proc.returncode}
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=3)
        self._procs.pop(run_id, None)
        return {"run_id": run_id, "status": "stopped", "exit_code": proc.returncode}

    def wait(self, run_id: str, timeout: float = 60) -> dict:
        proc = self._procs.get(run_id)
        if proc is None:
            raise AdapterError(f"unknown adapter run: {run_id}")
        try:
            code = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=2)
            self._procs.pop(run_id, None)
            raise AdapterError(f"adapter run timed out: {run_id}") from None
        self._procs.pop(run_id, None)
        return {"run_id": run_id, "status": "exited", "exit_code": code}

    def events(self, run_id: str) -> list[dict]:
        # CLI 适配器暂不做 stdout 解析；厂商适配器应实现结构化事件。
        return []


class CodexAdapter(CommandAdapter):
    """Codex App Server 适配器：长驻 JSON-RPC，通过 CODEX_APP_SERVER_CMD 注入。"""

    def __init__(self, command: list[str] | None = None):
        raw = os.environ.get("CODEX_APP_SERVER_CMD", "").strip()
        cmd = command or (shlex.split(raw) if raw else ["codex", "app-server"])
        super().__init__("codex", cmd)


class NdjsonAdapter(CommandAdapter):
    """把子进程 stdout 的 NDJSON 行收集成事件。"""

    accept_prompt = True

    def __init__(self, name: str, command: list[str] | None = None):
        super().__init__(name, command)
        self._events: dict[str, list[dict]] = {}

    def start(self, task_id: str, workspace: str, env: dict | None = None,
              prompt: str | None = None) -> dict:
        if not prompt:
            raise AdapterError(f"{self.name} requires a prompt")
        info = self.probe()
        if not info["available"]:
            raise AdapterError(info["reason"])
        cwd = Path(workspace).resolve()
        if not cwd.is_dir():
            raise AdapterError(f"workspace not found: {cwd}")
        merged_env = os.environ.copy()
        merged_env.update(env or {})
        cmd = [info["executable"], *self.command[1:], prompt]
        proc = subprocess.Popen(
            cmd,
            cwd=str(cwd),
            env=merged_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        run_id = "AR-" + uuid.uuid4().hex[:8]
        self._procs[run_id] = proc
        self._events[run_id] = []

        def read() -> None:
            if proc.stdout is None:
                return
            try:
                for line in proc.stdout:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        event = {"type": "raw", "line": line}
                    self._events.setdefault(run_id, []).append(event)
            finally:
                proc.stdout.close()

        threading.Thread(target=read, daemon=True).start()
        return {
            "adapter": self.name,
            "run_id": run_id,
            "task_id": task_id,
            "workspace": str(cwd),
            "pid": proc.pid,
            "status": "running",
            "events": "ndjson",
        }

    def events(self, run_id: str) -> list[dict]:
        return list(self._events.get(run_id, []))

    def usage(self, run_id: str) -> dict:
        return extract_usage(self.events(run_id))


class CodexExecAdapter(NdjsonAdapter):
    """Codex 非交互执行：codex exec --json "<task>"（pilot 用；App Server 另走 CodexAdapter）。"""

    def __init__(self, command: list[str] | None = None, check_login: bool = True):
        raw = os.environ.get("CODEX_EXEC_CMD", "").strip()
        cmd = command or (
            shlex.split(raw) if raw
            else ["codex", "exec", "--json", "--skip-git-repo-check",
                  "--sandbox", "danger-full-access"]
        )
        super().__init__("codex-exec", cmd)
        self.check_login = check_login

    def probe(self) -> dict:
        info = super().probe()
        if not info["available"] or not self.check_login:
            return info
        home = os.environ.get("CODEX_HOME")
        if home:
            cfg = Path(home) / "config.toml"
            if cfg.exists():
                text = cfg.read_text(encoding="utf-8", errors="replace")
                match = re.search(r'(?m)^model_provider\s*=\s*"([^"]+)"', text)
                if match and match.group(1) != "openai":
                    return {**info, "auth": f"custom provider: {match.group(1)}"}
        try:
            proc = subprocess.run(
                [info["executable"], "login", "status"],
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=10,
            )
        except subprocess.TimeoutExpired:
            return {**info, "available": False, "reason": "codex login status timed out"}
        if proc.returncode != 0:
            return {
                **info,
                "available": False,
                "reason": (proc.stdout or proc.stderr or "codex not logged in").strip(),
            }
        return {**info, "auth": "codex login"}


class _AppServerClient:
    """Codex App Server 的 JSON-RPC over stdio 客户端。

    事件读取与请求等待分离：所有通知先落盘再匹配，因此完成通知早于
    turn/start 应答也不会丢失。服务端请求（带 id 的 method）会立即按
    审批策略响应，未知请求明确报错，不会悬挂或默认批准。

    approval_handler 在读取线程中同步调用，必须快速返回；返回 None
    表示使用 fail-closed 默认拒绝。
    """

    # 已知审批请求的 fail-closed 默认响应；不提供批准分支。
    APPROVAL_DENIALS = {
        "item/commandExecution/requestApproval": {"decision": "decline"},
        "item/fileChange/requestApproval": {"decision": "decline"},
        "item/permissions/requestApproval": {"permissions": {}},
        "mcpServer/elicitation/request": {"action": "decline"},
        "applyPatchApproval": {
            "decision": {"denied": {"rejection": "agent-guild: approval not granted"}}
        },
        "execCommandApproval": {
            "decision": {"denied": {"rejection": "agent-guild: approval not granted"}}
        },
    }

    TERMINAL_TURN_STATUSES = ("completed", "failed", "interrupted")

    def __init__(
        self,
        proc: subprocess.Popen,
        approval_handler: Callable[[str, dict], dict | None] | None = None,
    ):
        self.proc = proc
        self.approval_handler = approval_handler
        self.notifications: list[dict] = []
        self.server_requests: list[dict] = []
        self.turn_completions: list[dict] = []
        self.malformed: list[dict] = []
        self.late_responses: list[dict] = []
        self._cond = threading.Condition()
        self._id = 0
        self._pending: dict[int, dict | None] = {}
        self._closed = False
        self._failure: Exception | None = None
        self._write_lock = threading.Lock()
        threading.Thread(target=self._read, daemon=True).start()

    # ---------- 读取与分发 ----------
    def _read(self) -> None:
        assert self.proc.stdout is not None
        try:
            for line in self.proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    with self._cond:
                        self.malformed.append({"raw": line})
                    continue
                if not isinstance(msg, dict):
                    with self._cond:
                        self.malformed.append({"raw": line})
                    continue
                try:
                    self._dispatch(msg)
                except Exception as exc:  # noqa: BLE001 - 保持读取线程存活到 finally
                    with self._cond:
                        self._failure = exc
                    break
        finally:
            with self._cond:
                self._closed = True
                self._cond.notify_all()
            try:
                self.proc.stdout.close()
            except OSError:
                pass

    def _dispatch(self, msg: dict) -> None:
        mid = msg.get("id")
        # bool 是 Python int 的子类，必须排除，避免 true 匹配请求编号 1。
        if "id" in msg and not (
            isinstance(mid, str) or (type(mid) is int and -(2**63) <= mid < 2**63)
        ):
            raise AdapterError("Invalid JSON-RPC identity")
        if "method" in msg:
            if "id" in msg:
                self._handle_server_request(msg)
            else:
                self._record_notification(msg)
            return
        if "id" not in msg or ("result" in msg) == ("error" in msg):
            raise AdapterError("Invalid JSON-RPC response: expected identity and exactly one result/error")
        if "error" in msg:
            error = msg["error"]
            if (
                not isinstance(error, dict)
                or type(error.get("code")) is not int
                or not isinstance(error.get("message"), str)
            ):
                raise AdapterError("Invalid JSON-RPC response: invalid error object")
        elif not isinstance(msg["result"], dict):
            raise AdapterError("Invalid JSON-RPC response: expected an object result")
        # 响应：只按请求 ID 关联；超时后到达的响应不能完成新的请求。
        with self._cond:
            if mid in self._pending and self._pending[mid] is None:
                self._pending[mid] = msg
            else:
                self.late_responses.append(msg)
            self._cond.notify_all()

    def _record_notification(self, msg: dict) -> None:
        with self._cond:
            self.notifications.append(msg)
            self._cond.notify_all()
        method = msg.get("method")
        if method == "turn/completed":
            self._record_completion(msg)

    def _record_completion(self, msg: dict) -> None:
        params = msg.get("params")
        if not isinstance(params, dict):
            with self._cond:
                self.malformed.append(msg)
            return
        thread_id = params.get("threadId")
        turn = params.get("turn")
        if (
            not isinstance(thread_id, str)
            or not isinstance(turn, dict)
            or not isinstance(turn.get("id"), str)
        ):
            with self._cond:
                self.malformed.append(msg)
            return
        status = turn.get("status")
        if status not in self.TERMINAL_TURN_STATUSES:
            # turn/completed 携带 inProgress 或缺失状态属于不完整/未知结果。
            status = "unknown"
        error = ""
        if isinstance(turn.get("error"), dict):
            error = str(turn["error"].get("message", ""))
        elif turn.get("error"):
            error = str(turn["error"])
        completion = {
            "thread_id": thread_id,
            "turn_id": turn["id"],
            "status": status,
            "error": error,
            "raw": msg,
        }
        with self._cond:
            self.turn_completions.append(completion)
            self._cond.notify_all()

    def observe_turn(self, thread_id: str, turn: object) -> None:
        """把 turn/start 响应里的终态也当作结果（通常是 inProgress）。"""
        if not isinstance(turn, dict) or not isinstance(turn.get("id"), str):
            return
        if turn.get("status") not in self.TERMINAL_TURN_STATUSES:
            return
        self._record_completion({
            "method": "turn/start.response",
            "params": {"threadId": thread_id, "turn": turn},
        })

    # ---------- 服务端请求 ----------
    def _handle_server_request(self, msg: dict) -> None:
        method = msg.get("method")
        mid = msg.get("id")
        params = msg.get("params")
        if not isinstance(params, dict):
            params = {}
        entry: dict = {
            "id": mid, "method": method, "params": params,
            "response": None, "error": None, "source": "",
        }
        decision = None
        if self.approval_handler is not None:
            try:
                decision = self.approval_handler(method, params)
            except Exception as exc:  # noqa: BLE001
                self._send_error(mid, -32603, f"approval handler failed: {exc}")
                entry["error"] = f"handler: {exc}"
                entry["source"] = "handler-error"
            else:
                if decision is not None:
                    entry["source"] = "handler"
        if decision is None and entry["error"] is None:
            if method in self.APPROVAL_DENIALS:
                decision = dict(self.APPROVAL_DENIALS[method])
                entry["source"] = "default-deny"
            else:
                self._send_error(mid, -32601, f"unsupported server request: {method}")
                entry["error"] = "unsupported"
                entry["source"] = "unsupported"
        if decision is not None:
            self._send_result(mid, decision)
            entry["response"] = decision
        with self._cond:
            self.server_requests.append(entry)
            self._cond.notify_all()

    # ---------- 写入 ----------
    def _send(self, frame: dict) -> None:
        if self.proc.stdin is None:
            raise AdapterError("app-server stdin is not available")
        with self._write_lock:
            try:
                self.proc.stdin.write(json.dumps(frame) + "\n")
                self.proc.stdin.flush()
            except (BrokenPipeError, OSError) as exc:
                raise AdapterError(f"app-server stdin write failed: {exc}") from exc

    def _send_result(self, mid: object, result: dict) -> None:
        self._send({"jsonrpc": "2.0", "id": mid, "result": result})

    def _send_error(self, mid: object, code: int, message: str) -> None:
        self._send({"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}})

    # ---------- 请求与等待 ----------
    def request(self, method: str, params: dict, timeout: float = 30) -> dict:
        with self._cond:
            if self._closed:
                if self._failure is not None:
                    raise AdapterError(str(self._failure)) from self._failure
                raise AdapterError(f"app-server closed before {method}")
            self._id += 1
            mid = self._id
            self._pending[mid] = None
        try:
            self._send({"jsonrpc": "2.0", "id": mid, "method": method, "params": params})
        except Exception:
            with self._cond:
                self._pending.pop(mid, None)
            raise

        deadline = time.time() + timeout
        with self._cond:
            while True:
                response = self._pending.get(mid)
                if response is not None:
                    self._pending.pop(mid, None)
                    break
                if self._closed:
                    self._pending.pop(mid, None)
                    if self._failure is not None:
                        raise AdapterError(str(self._failure)) from self._failure
                    raise AdapterError(f"app-server closed during {method}")
                remaining = deadline - time.time()
                if remaining <= 0:
                    self._pending.pop(mid, None)
                    raise AdapterError(
                        f"app-server {method} timed out; execution state is unknown"
                    )
                self._cond.wait(min(remaining, 0.1))
        if "error" in response:
            raise AdapterError(f"app-server {method} failed: {response['error']}")
        return response["result"]

    def wait_turn(self, thread_id: str, turn_id: str, timeout: float = 180) -> dict:
        """等待指定 thread/turn 的终态；超时返回 unknown，不重发、不判成功。"""
        deadline = time.time() + timeout
        with self._cond:
            while True:
                for completion in self.turn_completions:
                    if (
                        completion["thread_id"] == thread_id
                        and completion["turn_id"] == turn_id
                    ):
                        return {
                            "status": completion["status"],
                            "thread_id": thread_id,
                            "turn_id": turn_id,
                            "error": completion["error"],
                            "reason": "completed",
                        }
                if self._closed:
                    return {
                        "status": "closed",
                        "thread_id": thread_id,
                        "turn_id": turn_id,
                        "error": "app-server exited before turn completed",
                        "reason": "closed",
                    }
                remaining = deadline - time.time()
                if remaining <= 0:
                    return {
                        "status": "unknown",
                        "thread_id": thread_id,
                        "turn_id": turn_id,
                        "error": "turn did not complete before timeout; execution state is unknown",
                        "reason": "timeout",
                    }
                self._cond.wait(min(remaining, 0.1))

    def interrupt_turn(self, thread_id: str, turn_id: str, timeout: float = 5) -> bool:
        try:
            self.request("turn/interrupt", {"threadId": thread_id, "turnId": turn_id}, timeout)
            return True
        except AdapterError:
            return False


class CodexAppServerAdapter(BaseAdapter):
    """真实 Codex App Server：initialize → thread/start → turn/start。

    默认使用 workspace-write + on-request，保留审批通道；不再写死
    danger-full-access / approvalPolicy=never。sandbox 只允许通过
    CODEX_APP_SERVER_SANDBOX 显式覆盖，审批策略始终为 on-request。
    """

    name = "codex-appserver"
    DEFAULT_SANDBOX = "workspace-write"
    APPROVAL_POLICY = "on-request"
    SANDBOX_MODES = ("read-only", "workspace-write", "danger-full-access")

    def __init__(
        self,
        command: list[str] | None = None,
        approval_handler: Callable[[str, dict], dict | None] | None = None,
        late_event_grace: float = 0.2,
    ):
        raw = os.environ.get("CODEX_APP_SERVER_CMD", "").strip()
        cmd = command or (
            shlex.split(raw) if raw
            else ["codex", "app-server", "--listen", "stdio://"]
        )
        self.command = cmd
        self.approval_handler = approval_handler
        self.late_event_grace = max(0.0, float(late_event_grace))
        self._runs: dict[str, dict] = {}
        self._finished: dict[str, list[dict]] = {}
        self._finished_server_requests: dict[str, list[dict]] = {}

    def sandbox_mode(self) -> str:
        value = os.environ.get("CODEX_APP_SERVER_SANDBOX", self.DEFAULT_SANDBOX).strip()
        if value not in self.SANDBOX_MODES:
            raise AdapterError(f"invalid CODEX_APP_SERVER_SANDBOX: {value}")
        return value

    def probe(self) -> dict:
        candidate = Path(self.command[0])
        exe = str(candidate) if candidate.is_file() else shutil.which(self.command[0])
        if exe is None:
            return {"name": self.name, "available": False,
                    "reason": f"executable not found: {self.command[0]}"}
        auth = "unknown"
        home = os.environ.get("CODEX_HOME")
        if home:
            cfg = Path(home) / "config.toml"
            if cfg.exists():
                text = cfg.read_text(encoding="utf-8", errors="replace")
                match = re.search(r'(?m)^model_provider\s*=\s*"([^"]+)"', text)
                if match:
                    auth = f"provider: {match.group(1)}"
        return {"name": self.name, "available": True, "executable": exe,
                "command": self.command, "auth": auth, "transport": "stdio",
                "sandbox": os.environ.get("CODEX_APP_SERVER_SANDBOX", self.DEFAULT_SANDBOX),
                "approval_policy": self.APPROVAL_POLICY}

    def start(self, task_id: str, workspace: str, env: dict | None = None,
              prompt: str | None = None) -> dict:
        if not prompt:
            raise AdapterError("Codex App Server requires a prompt")
        info = self.probe()
        if not info["available"]:
            raise AdapterError(info["reason"])
        cwd = Path(workspace).resolve()
        if not cwd.is_dir():
            raise AdapterError(f"workspace not found: {cwd}")
        sandbox = self.sandbox_mode()
        merged_env = os.environ.copy()
        merged_env.update(env or {})
        proc = subprocess.Popen(
            [info["executable"], *self.command[1:]],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding="utf-8", errors="replace",
            env=merged_env, cwd=str(cwd),
        )
        client = _AppServerClient(proc, approval_handler=self.approval_handler)
        run_id = "AR-" + uuid.uuid4().hex[:8]
        try:
            client.request("initialize", {
                "clientInfo": {"name": "agent-guild", "version": "0.1.0"},
            }, timeout=20)
            thread = client.request("thread/start", {
                "cwd": str(cwd),
                "sandbox": sandbox,
                "approvalPolicy": self.APPROVAL_POLICY,
                "approvalsReviewer": "user",
            }, timeout=30)
            thread_data = thread.get("thread")
            if not isinstance(thread_data, dict) or not isinstance(thread_data.get("id"), str):
                raise AdapterError("Codex app-server did not return a thread identity")
            thread_id = thread_data["id"]
            turn = client.request("turn/start", {
                "threadId": thread_id,
                "cwd": str(cwd),
                "input": [{"type": "text", "text": prompt}],
            }, timeout=30)
        except Exception:
            _terminate_process(proc)
            raise
        turn_data = turn.get("turn")
        if not isinstance(turn_data, dict) or not isinstance(turn_data.get("id"), str):
            _terminate_process(proc)
            raise AdapterError("Codex app-server did not return a turn identity")
        turn_id = turn_data["id"]
        client.observe_turn(thread_id, turn_data)
        self._runs[run_id] = {
            "proc": proc, "client": client, "thread_id": thread_id,
            "turn_id": turn_id, "status": "running", "sandbox": sandbox,
        }
        return {
            "adapter": self.name, "run_id": run_id, "task_id": task_id,
            "workspace": str(cwd), "pid": proc.pid, "status": "running",
            "thread_id": thread_id, "turn_id": turn_id,
            "sandbox": sandbox, "approval_policy": self.APPROVAL_POLICY,
            "events": "jsonrpc",
        }

    def wait(self, run_id: str, timeout: float = 180) -> dict:
        item = self._runs.get(run_id)
        if item is None:
            raise AdapterError(f"unknown adapter run: {run_id}")
        client = item["client"]
        turn_result = client.wait_turn(item["thread_id"], item["turn_id"], timeout)
        if turn_result["status"] == "unknown":
            # 超时后先尝试取消，并给迟到事件一个短暂窗口；结果仍保持 unknown。
            client.interrupt_turn(item["thread_id"], item["turn_id"], timeout=2)
            if self.late_event_grace:
                time.sleep(self.late_event_grace)
        proc = item["proc"]
        code = _terminate_process(proc)
        item["status"] = turn_result["status"]
        self._finished[run_id] = list(client.notifications)
        self._finished_server_requests[run_id] = list(client.server_requests)
        self._runs.pop(run_id, None)
        status = turn_result["status"]
        error = turn_result.get("error", "")
        if status == "interrupted" and not error:
            error = "turn interrupted"
        if status == "unknown" and not error:
            error = "turn reported an incomplete/unknown status"
        return {
            "run_id": run_id,
            "status": status,
            "exit_code": 0 if status == "completed" else (code if code else 1),
            "thread_id": item["thread_id"],
            "turn_id": item["turn_id"],
            "error": error,
            "sandbox": item.get("sandbox", self.DEFAULT_SANDBOX),
            "approval_policy": self.APPROVAL_POLICY,
        }

    def events(self, run_id: str) -> list[dict]:
        item = self._runs.get(run_id)
        if item is not None:
            return list(item["client"].notifications)
        return list(self._finished.get(run_id, []))

    def server_requests(self, run_id: str) -> list[dict]:
        item = self._runs.get(run_id)
        if item is not None:
            return list(item["client"].server_requests)
        return list(self._finished_server_requests.get(run_id, []))

    def stop(self, run_id: str) -> dict:
        item = self._runs.pop(run_id, None)
        if item is None:
            raise AdapterError(f"unknown adapter run: {run_id}")
        client = item["client"]
        confirmed = False
        turn_status = None
        if item["turn_id"]:
            # 收到 interrupt 应答只表示请求已被接收，不能证明 turn 已结束。
            client.interrupt_turn(item["thread_id"], item["turn_id"], timeout=5)
            result = client.wait_turn(item["thread_id"], item["turn_id"], timeout=2)
            turn_status = result["status"]
            if turn_status in ("interrupted", "completed", "failed"):
                confirmed = True
        _terminate_process(item["proc"])
        self._finished[run_id] = list(client.notifications)
        self._finished_server_requests[run_id] = list(client.server_requests)
        return {
            "run_id": run_id,
            "status": "stopped",
            "turn_status": turn_status,
            "confirmed": confirmed,
        }


def _terminate_process(proc: subprocess.Popen) -> int | None:
    if proc.poll() is not None:
        code = proc.returncode
    else:
        proc.terminate()
        try:
            code = proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            try:
                code = proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                code = None
    try:
        if proc.stdin is not None and not proc.stdin.closed:
            proc.stdin.close()
    except (OSError, ValueError):
        pass
    return code


class DshAdapter(NdjsonAdapter):
    """DSH headless 适配器：dsh --profile headless --json "<task>"。"""

    def __init__(self, command: list[str] | None = None):
        raw = os.environ.get("DSH_SDK_CMD", "").strip()
        cmd = command or (shlex.split(raw) if raw else ["dsh", "--profile", "headless", "--json"])
        super().__init__("dsh", cmd)

    def probe(self) -> dict:
        info = super().probe()
        if not info["available"]:
            return info
        if not os.environ.get("DEEPSEEK_API_KEY"):
            return {
                **info,
                "available": False,
                "reason": "DEEPSEEK_API_KEY missing (or configure DSH Models credentials)",
            }
        return {**info, "mode": "headless", "auth": "env:DEEPSEEK_API_KEY"}


class MockAdapter(BaseAdapter):
    name = "mock"

    def __init__(self):
        self.runs: dict[str, dict] = {}

    def probe(self) -> dict:
        return {"name": "mock", "available": True, "reason": "test adapter"}

    def start(self, task_id: str, workspace: str, env: dict | None = None,
              prompt: str | None = None) -> dict:
        run_id = "AR-" + uuid.uuid4().hex[:8]
        self.runs[run_id] = {"task_id": task_id, "workspace": workspace, "status": "running"}
        return {"adapter": "mock", "run_id": run_id, "task_id": task_id,
                "workspace": workspace, "status": "running"}

    def stop(self, run_id: str) -> dict:
        if run_id not in self.runs:
            raise AdapterError(f"unknown adapter run: {run_id}")
        self.runs[run_id]["status"] = "stopped"
        return {"run_id": run_id, "status": "stopped"}

    def events(self, run_id: str) -> list[dict]:
        return [{"type": "mock", "run_id": run_id, "status": self.runs.get(run_id, {}).get("status")}]


def extract_usage(events: list[dict]) -> dict:
    """从 NDJSON 事件里尽量提取 usage；找不到就返回空。

    DSH/Codex 的事件结构可能不同，这里做宽松递归：只认常见字段，取最大值，
    避免把累计值重复相加。
    """
    keys = {
        "input_tokens": "input_tokens", "inputtokens": "input_tokens",
        "output_tokens": "output_tokens", "outputtokens": "output_tokens",
        "total_tokens": "total_tokens", "totaltokens": "total_tokens",
        "cached_input_tokens": "cached_input_tokens",
        "cachereadtokens": "cached_input_tokens",
        "cachedinputtokens": "cached_input_tokens",
        "cache_write_tokens": "cache_write_tokens",
        "cachewritetokens": "cache_write_tokens",
        "cachewriteinputtokens": "cache_write_tokens",
        "reasoning_tokens": "reasoning_tokens",
        "reasoningoutputtokens": "reasoning_tokens",
        "cost_usd": "cost_usd", "total_cost_usd": "total_cost_usd",
    }
    values: dict[str, float] = {}

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                lower = key.lower()
                canonical = keys.get(lower)
                if canonical and isinstance(value, (int, float)):
                    values[canonical] = max(values.get(canonical, 0.0), float(value))
                else:
                    walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(events)
    out = {}
    for key in ("input_tokens", "output_tokens", "total_tokens",
                "cached_input_tokens", "cache_write_tokens", "reasoning_tokens"):
        if key in values:
            out[key] = int(values[key])
    if "cost_usd" in values:
        out["cost_usd"] = round(values["cost_usd"], 6)
    elif "total_cost_usd" in values:
        out["cost_usd"] = round(values["total_cost_usd"], 6)
    return out


def all_adapters() -> dict[str, BaseAdapter]:
    return {
        "codex": CodexAdapter(),
        "codex-exec": CodexExecAdapter(),
        "codex-appserver": CodexAppServerAdapter(),
        "dsh": DshAdapter(),
        "mock": MockAdapter(),
    }


def probe_all() -> list[dict]:
    return [adapter.probe() for adapter in all_adapters().values()]
