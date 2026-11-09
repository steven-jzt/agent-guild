"""Agent adapter 合同：Codex / DSH / 通用命令 / Mock。

设计原则：
- 适配器只负责启动、停止、探测、事件；不负责验收、合入、释放预约。
- 厂商未提供的能力明确返回 unsupported，不伪造。
- 真实接入需要配置命令/凭据；默认 probe 返回 unavailable。
"""

from __future__ import annotations

import json
import os
import queue
import re
import shlex
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Any


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
    """Codex App Server 的 JSON-RPC over stdio 最小客户端。"""

    def __init__(self, proc: subprocess.Popen):
        self.proc = proc
        self.q: queue.Queue[dict | None] = queue.Queue()
        self.notifications: list[dict] = []
        self._id = 0
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self) -> None:
        assert self.proc.stdout is not None
        try:
            for line in self.proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    self.q.put(json.loads(line))
                except json.JSONDecodeError:
                    self.q.put({"raw": line})
        finally:
            self.q.put(None)
            self.proc.stdout.close()

    def request(self, method: str, params: dict, timeout: float = 30) -> dict:
        self._id += 1
        req = {"jsonrpc": "2.0", "id": self._id, "method": method, "params": params}
        assert self.proc.stdin is not None
        self.proc.stdin.write(json.dumps(req) + "\n")
        self.proc.stdin.flush()
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                msg = self.q.get(timeout=max(0.1, deadline - time.time()))
            except queue.Empty:
                break
            if msg is None:
                raise AdapterError(f"app-server closed during {method}")
            if msg.get("id") == self._id:
                if "error" in msg:
                    raise AdapterError(f"app-server {method} failed: {msg['error']}")
                return msg.get("result", {})
            if "method" in msg:
                self.notifications.append(msg)
        raise AdapterError(f"app-server {method} timed out")

    def wait_turn(self, timeout: float = 180) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                msg = self.q.get(timeout=max(0.1, deadline - time.time()))
            except queue.Empty:
                break
            if msg is None:
                break
            if "method" in msg:
                self.notifications.append(msg)
                if msg["method"] == "turn/completed":
                    return True
                if msg["method"] in ("turn/failed", "error"):
                    return False
        return False


class CodexAppServerAdapter(BaseAdapter):
    """真实 Codex App Server：initialize → thread/start → turn/start。"""

    name = "codex-appserver"

    def __init__(self, command: list[str] | None = None):
        raw = os.environ.get("CODEX_APP_SERVER_CMD", "").strip()
        cmd = command or (
            shlex.split(raw) if raw
            else ["codex", "app-server", "--listen", "stdio://"]
        )
        self.command = cmd
        self._runs: dict[str, dict] = {}
        self._finished: dict[str, list[dict]] = {}

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
                "command": self.command, "auth": auth, "transport": "stdio"}

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
        merged_env = os.environ.copy()
        merged_env.update(env or {})
        proc = subprocess.Popen(
            [info["executable"], *self.command[1:]],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding="utf-8", errors="replace",
            env=merged_env, cwd=str(cwd),
        )
        client = _AppServerClient(proc)
        run_id = "AR-" + uuid.uuid4().hex[:8]
        try:
            client.request("initialize", {
                "clientInfo": {"name": "agent-guild", "version": "0.1.0"},
            }, timeout=20)
            thread = client.request("thread/start", {
                "cwd": str(cwd),
                "sandbox": "danger-full-access",
                "approvalPolicy": "never",
            }, timeout=30)
            thread_id = thread["thread"]["id"]
            turn = client.request("turn/start", {
                "threadId": thread_id,
                "cwd": str(cwd),
                "input": [{"type": "text", "text": prompt}],
            }, timeout=30)
        except Exception:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
            raise
        self._runs[run_id] = {
            "proc": proc, "client": client, "thread_id": thread_id,
            "turn_id": (turn.get("turn") or {}).get("id"), "status": "running",
        }
        return {
            "adapter": self.name, "run_id": run_id, "task_id": task_id,
            "workspace": str(cwd), "pid": proc.pid, "status": "running",
            "thread_id": thread_id, "turn_id": self._runs[run_id]["turn_id"],
            "events": "jsonrpc",
        }

    def wait(self, run_id: str, timeout: float = 180) -> dict:
        item = self._runs.get(run_id)
        if item is None:
            raise AdapterError(f"unknown adapter run: {run_id}")
        ok = item["client"].wait_turn(timeout)
        proc = item["proc"]
        try:
            proc.terminate()
            code = proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            code = proc.wait(timeout=5)
        item["status"] = "completed" if ok else "failed"
        self._finished[run_id] = list(item["client"].notifications)
        self._runs.pop(run_id, None)
        return {"run_id": run_id, "status": item["status"], "exit_code": 0 if ok else code}

    def events(self, run_id: str) -> list[dict]:
        item = self._runs.get(run_id)
        if item is not None:
            return list(item["client"].notifications)
        return list(self._finished.get(run_id, []))

    def stop(self, run_id: str) -> dict:
        item = self._runs.pop(run_id, None)
        if item is None:
            raise AdapterError(f"unknown adapter run: {run_id}")
        item["proc"].terminate()
        try:
            item["proc"].wait(timeout=5)
        except subprocess.TimeoutExpired:
            item["proc"].kill()
        return {"run_id": run_id, "status": "stopped"}


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
