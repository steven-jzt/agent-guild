# contention —— 共享文件竞争实验

验证「同一工作空间、多个 agent/进程同时改同一个文件」会发生什么，以及加锁后是否可靠。

## 脚本

- `naive_increment.py`：模拟 agent 的朴素行为——读 JSON、想一会儿、整文件覆盖写回。无锁、无版本检查。
- `locked_increment.py`：同样的自增，但用原子 `mkdir` 作为互斥锁。

## 实验

期望值 = 进程数 × 每进程次数。三个进程各 60 次，期望 180。

```powershell
'{"count": 0}' | Set-Content counter_naive.json -Encoding utf8
# 并发启动 3 个 naive_increment.py，然后看 counter_naive.json
```

结果记录见 workspace 根目录的实验报告。
