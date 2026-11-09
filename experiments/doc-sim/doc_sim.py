"""文档并发编辑实验：3 个 agent 改同一份 Markdown。

两种场景：
1. append：12 个任务各追加一行唯一标记，测“丢更新 / 吞吐”；
2. replace：3 个 agent 抢改同一行 Status，测“是否发现意图冲突”。

四种策略：
- none    无协调，最后写赢；
- global  全局互斥锁；
- section 章节级租约（每节一把锁）；
- mvcc    快照 + 版本校验 + 追加合并（COW 思路）。

这是机制级仿真，不是真实 LLM；目的是比较协议，不是比较模型。
"""

from __future__ import annotations

import random
import threading
import time
from dataclasses import dataclass

SECTIONS = ["S1", "S2", "S3", "S4", "S5", "S6"]
BASE = {s: [f"## {s}", "Status: draft", "- base note"] for s in SECTIONS}
AGENTS = 3


@dataclass
class Task:
    id: str
    section: str
    marker: str
    kind: str = "append"  # append | replace
    payload: str = ""


def make_append_tasks() -> list[Task]:
    sections = ["S1", "S1", "S2", "S2", "S3", "S3", "S4", "S4", "S5", "S6", "S1", "S2"]
    return [
        Task(id=f"T{i}", section=sec, marker=f"- edit-{i}")
        for i, sec in enumerate(sections)
    ]


def make_replace_tasks() -> list[Task]:
    return [
        Task(id=f"R{i}", section="S1" if i < 3 else "S2", marker="", kind="replace", payload=f"Status: v{i}")
        for i in range(6)
    ]


class Doc:
    def __init__(self) -> None:
        self.lines = {s: list(BASE[s]) for s in SECTIONS}
        self.versions = {s: 0 for s in SECTIONS}
        self.lock = threading.Lock()

    def read(self, section: str):
        with self.lock:
            return list(self.lines[section]), self.versions[section]

    def write(self, section: str, lines: list[str]) -> None:
        with self.lock:
            self.lines[section] = lines
            self.versions[section] += 1

    def apply_append(self, section: str, marker: str) -> None:
        with self.lock:
            self.lines[section] = self.lines[section] + [marker]
            self.versions[section] += 1

    def apply_replace(self, section: str, payload: str) -> None:
        with self.lock:
            lines = [
                payload if line.startswith("Status:") else line
                for line in self.lines[section]
            ]
            self.lines[section] = lines
            self.versions[section] += 1


def run_append(protocol: str, seed: int) -> dict:
    rng = random.Random(seed)
    doc = Doc()
    global_lock = threading.Lock()
    section_locks = {s: threading.Lock() for s in SECTIONS}
    retries = 0
    retry_lock = threading.Lock()

    tasks = make_append_tasks()
    assignments = [tasks[i::AGENTS] for i in range(AGENTS)]

    def work(batch: list[Task]) -> None:
        nonlocal retries
        for task in batch:
            if protocol == "none":
                lines, _ = doc.read(task.section)
                time.sleep(rng.uniform(0.003, 0.008))
                doc.write(task.section, lines + [task.marker])

            elif protocol == "global":
                with global_lock:
                    lines, _ = doc.read(task.section)
                    time.sleep(rng.uniform(0.003, 0.008))
                    doc.write(task.section, lines + [task.marker])

            elif protocol == "section":
                with section_locks[task.section]:
                    lines, _ = doc.read(task.section)
                    time.sleep(rng.uniform(0.003, 0.008))
                    doc.write(task.section, lines + [task.marker])

            elif protocol == "mvcc":
                lines, ver = doc.read(task.section)
                time.sleep(rng.uniform(0.003, 0.008))
                with doc.lock:
                    if doc.versions[task.section] == ver:
                        doc.lines[task.section] = lines + [task.marker]
                        doc.versions[task.section] += 1
                    else:
                        # append-only 可合并：把标记追加到当前版本
                        doc.lines[task.section] = doc.lines[task.section] + [task.marker]
                        doc.versions[task.section] += 1
                        with retry_lock:
                            retries += 1
            else:
                raise ValueError(protocol)

    started = time.perf_counter()
    threads = [threading.Thread(target=work, args=(batch,)) for batch in assignments]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    elapsed = time.perf_counter() - started

    preserved = sum(
        1 for task in tasks if task.marker in doc.lines[task.section]
    )
    return {
        "protocol": protocol,
        "preserved": preserved,
        "lost": len(tasks) - preserved,
        "retries": retries,
        "ms": elapsed * 1000,
    }


def run_replace(protocol: str) -> dict:
    doc = Doc()
    global_lock = threading.Lock()
    section_locks = {s: threading.Lock() for s in SECTIONS}
    detected = 0
    detect_lock = threading.Lock()
    tasks = make_replace_tasks()
    assignments = [tasks[i::AGENTS] for i in range(AGENTS)]

    def work(batch: list[Task]) -> None:
        nonlocal detected
        for task in batch:
            if protocol == "none":
                _, _ = doc.read(task.section)
                time.sleep(0.004)
                doc.apply_replace(task.section, task.payload)

            elif protocol == "global":
                with global_lock:
                    doc.read(task.section)
                    time.sleep(0.004)
                    doc.apply_replace(task.section, task.payload)

            elif protocol == "section":
                with section_locks[task.section]:
                    doc.read(task.section)
                    time.sleep(0.004)
                    doc.apply_replace(task.section, task.payload)

            elif protocol == "mvcc":
                _, ver = doc.read(task.section)
                time.sleep(0.004)
                with doc.lock:
                    if doc.versions[task.section] != ver:
                        with detect_lock:
                            detected += 1
                        continue  # 发现冲突，放弃覆盖（交给人工/合并）
                    doc.lines[task.section] = [
                        task.payload if line.startswith("Status:") else line
                        for line in doc.lines[task.section]
                    ]
                    doc.versions[task.section] += 1
            else:
                raise ValueError(protocol)

    threads = [threading.Thread(target=work, args=(batch,)) for batch in assignments]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    conflict_intents = 4  # S1 两个多余意图 + S2 两个多余意图
    silent = conflict_intents - detected
    return {
        "protocol": protocol,
        "detected": detected,
        "silent": silent,
        "s1": next(x for x in doc.lines["S1"] if x.startswith("Status:")),
        "s2": next(x for x in doc.lines["S2"] if x.startswith("Status:")),
    }


def main() -> int:
    seeds = 30
    print("=== append scenario: 12 tasks / 3 agents / 6 sections ===")
    print(f"{'protocol':9} {'preserved':>9} {'lost':>5} {'retries':>7} {'ms':>7}")
    for protocol in ("none", "global", "section", "mvcc"):
        rows = [run_append(protocol, seed) for seed in range(seeds)]
        print(
            f"{protocol:9} "
            f"{sum(r['preserved'] for r in rows) / seeds:9.1f} "
            f"{sum(r['lost'] for r in rows) / seeds:5.1f} "
            f"{sum(r['retries'] for r in rows) / seeds:7.1f} "
            f"{sum(r['ms'] for r in rows) / seeds:7.1f}"
        )

    print("\n=== replace scenario: 3 agents rewrite the same Status line ===")
    print(f"{'protocol':9} {'detected':>9} {'silent':>7} {'S1':>12} {'S2':>12}")
    for protocol in ("none", "global", "section", "mvcc"):
        r = run_replace(protocol)
        print(
            f"{protocol:9} {r['detected']:9d} {r['silent']:7d} "
            f"{r['s1']:>12} {r['s2']:>12}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
