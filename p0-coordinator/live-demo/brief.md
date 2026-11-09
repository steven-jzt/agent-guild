# Live Demo Brief

You are one worker in a 3-agent live demo. Each worker writes exactly one file.

## Rules

- Only create/edit the file assigned to you.
- Do not edit other files, `live.db`, or `setup.py`.
- The file must contain exactly two lines:
  1. `# Part X`（X is your letter）
  2. one English sentence containing the word **coordination**
- Then commit your task via the coordinator CLI with a `--run` JSON.

## Coordinator

DB: `p0-coordinator/live-demo/live.db`
CLI: `p0-coordinator/worker_cli.py`

Claim your task by `--task <your task id>`; use the returned `lease.fencing_token`.
Commit with the simple flags (avoid JSON quoting issues in Windows PowerShell):

```powershell
python worker_cli.py --db live-demo/live.db commit --task <task id> --worker <worker> --token <token> --duration 5 --quality 1.0 --diff-lines 3 --files-changed 1
```
