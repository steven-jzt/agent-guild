# Verified Live Demo Brief

You are one worker in a 3-agent demo. Each worker writes exactly one file.

## Rules

- Only create/edit your assigned file. Do not touch other files, `verified.db`, or `setup.py`.
- File must contain exactly two lines:
  1. `# Part X`
  2. one English sentence containing the word **coordination**
- Commit via CLI. **Use the simple flags** (avoid PowerShell JSON quoting):
  `--duration 5 --quality 1.0 --diff-lines 3 --files-changed 1`
- After commit your task will be `verification`, not `done`；独立 verifier 会跑 checks。

## Paths

- CLI: `C:\Users\steve\Desktop\探索区\p0-coordinator\worker_cli.py`
- DB: `C:\Users\steve\Desktop\探索区\p0-coordinator\live-demo\verified\verified.db`
- 你的文件：`C:\Users\steve\Desktop\探索区\p0-coordinator\live-demo\verified\part-x.md`
