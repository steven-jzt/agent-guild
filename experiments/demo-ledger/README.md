# demo-ledger

一个小型个人记账 CLI，用来做「多个 agent 在同一个工作空间协作」的实验。

- 三个实现 agent 并行开发 `storage.py` / `report.py` / `cli.py`，各自拥有独立文件；
- 编排者先写 `CONTRACT.md`（接口契约）与 `test_acceptance.py`（验收测试），再分发任务；
- 集成后由编排者（或 integrator agent）跑验收测试并修复跨模块问题。

## 运行

```bash
python cli.py add --amount 12.34 --category 餐饮 --note 午饭
python cli.py list
python cli.py report
python -m unittest test_acceptance -v
```

数据默认写到 `data/ledger.jsonl`，可用环境变量 `LEDGER_DATA` 覆盖。
