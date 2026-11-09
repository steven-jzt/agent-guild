# a2a-lab

用官方 `a2a-sdk` 1.3.0（A2A 协议 v1.0）做的双 agent 演示：

- `ledger_agent.py`：账本 agent（A2A server，127.0.0.1:8765）；
- `audit_agent.py`：审计 agent（A2A server，127.0.0.1:8766），内部通过 A2A 调 ledger；
- `demo.py`：客户端，先写三笔账，再让 audit 去审计。

## 运行

```powershell
# 终端 1
python ledger_agent.py

# 终端 2
python audit_agent.py

# 终端 3
python demo.py
```

预期最后一行：`audit.ok = True（audit agent 自己调用了 ledger agent，未经过客户端）`。

依赖已装在 `vendor/`，不需要动系统环境。`vendor/` 可删除后重建：

```powershell
pip install --target vendor "a2a-sdk[http-server]==1.3.0" uvicorn
```

> 本机沙箱下 pip 的临时目录会因 0o700 ACL 不可写；装包时可用
> `PYTHONPATH=bootstrap` 让 `sitecustomize.py` 放宽 `os.mkdir` 的 mode。

完整分析见 [../../docs/报告/A2A-可行性-附记.md](../../docs/报告/A2A-可行性-附记.md)。
