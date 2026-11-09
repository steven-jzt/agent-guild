# Governance Live Demo Brief

You are one worker in a 3-agent demo. Each worker writes exactly one file.

## Rules

- Only create/edit your assigned file. Do not touch other files, `governance.db`, or `setup.py`.
- File must contain exactly two lines:
  1. `# Part X`
  2. one English sentence containing the word **coordination**
- Commit with simple flags:
  `--duration 5 --quality 1.0 --diff-lines 3 --files-changed 1`
- 提交后状态是 `verification`，不是 done；verifier 通过后还会进入 2-of-2 审批。

## Paths

- CLI: `C:\Users\steve\Desktop\探索区\p0-coordinator\worker_cli.py`
- DB: `C:\Users\steve\Desktop\探索区\p0-coordinator\live-demo\governance\governance.db`
- 你的文件：`C:\Users\steve\Desktop\探索区\p0-coordinator\live-demo\governance\part-x.md`
