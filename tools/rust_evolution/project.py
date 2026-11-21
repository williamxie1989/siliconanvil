# SPDX-License-Identifier: Apache-2.0
"""Local task state and evidence for the oMLX Rust evolution project."""

import argparse
import json
import sqlite3
import subprocess
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB = Path.home() / "omlx-notes/rust-evolution/progress.sqlite3"
SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY,
    task TEXT NOT NULL,
    at TEXT NOT NULL,
    head TEXT NOT NULL,
    action TEXT NOT NULL,
    state TEXT,
    summary TEXT NOT NULL,
    evidence TEXT NOT NULL,
    next TEXT
);
CREATE INDEX IF NOT EXISTS events_task ON events(task, id);
"""


def catalog():
    tasks = {}
    path = Path(__file__).with_name("tasks.tsv")
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        task, prerequisites, title = line.split("\t")
        depends = prerequisites.split(",") if prerequisites else []
        if task in tasks or any(dep not in tasks for dep in depends):
            raise ValueError(f"任务重复，或前置任务未先定义: {task}")
        tasks[task] = {"id": task, "title": title, "depends": depends}
    return tasks


def history(db, task=None, query=None, limit=20):
    rows = db.execute(
        """
        SELECT * FROM events
        WHERE (? IS NULL OR task = ?)
          AND (? IS NULL OR instr(lower(summary || ' ' || evidence || ' ' ||
              coalesce(next, '') || ' ' || task), lower(?)) > 0)
        ORDER BY id DESC LIMIT ?
        """,
        (task, task, query, query, limit),
    )
    events = []
    for row in rows:
        event = dict(row)
        event["evidence"] = json.loads(event["evidence"])
        events.append(event)
    return events


def progress(db, tasks):
    states = dict(
        db.execute(
            "SELECT task, state FROM events WHERE id IN ("
            "SELECT max(id) FROM events WHERE state IS NOT NULL GROUP BY task)"
        ).fetchall()
    )
    next_steps = dict(
        db.execute(
            "SELECT task, next FROM events WHERE id IN ("
            "SELECT max(id) FROM events WHERE next IS NOT NULL OR state = 'done' "
            "GROUP BY task)"
        ).fetchall()
    )
    return {
        task: {
            **definition,
            "state": states.get(task, "pending"),
            "waiting": [
                dep for dep in definition["depends"] if states.get(dep) != "done"
            ],
            "next": next_steps.get(task),
        }
        for task, definition in tasks.items()
    }


def write_event(db, task, action, args, current):
    states = {
        "start": {"pending", "blocked"},
        "note": {"active", "blocked", "done"},
        "block": {"active"},
        "finish": {"active"},
    }
    if current["state"] not in states[action]:
        raise ValueError(f"{task} 当前状态 {current['state']}，无法执行 {action}")
    if action == "start" and current["waiting"]:
        raise ValueError(f"{task} 等待 {', '.join(current['waiting'])}")
    summary = (args.summary or "").strip()
    if action != "start" and not summary:
        raise ValueError("请提供 --summary")
    if action == "finish" and not args.evidence:
        raise ValueError("请提供至少一个 --evidence 文件或目录")

    evidence = []
    for value in args.evidence:
        path = Path(value).expanduser().resolve()
        if not path.exists():
            raise ValueError(f"证据路径不存在: {path}")
        evidence.append(str(path))

    head = subprocess.check_output(
        ["git", "-C", str(args.repo.expanduser()), "rev-parse", "HEAD"],
        text=True,
    ).strip()
    state = {"start": "active", "block": "blocked", "finish": "done"}.get(action)
    db.execute(
        "INSERT INTO events(task, at, head, action, state, summary, evidence, next)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            task,
            datetime.now(UTC).isoformat(timespec="seconds"),
            head,
            action,
            state,
            summary,
            json.dumps(evidence, ensure_ascii=False),
            args.next.strip() if args.next else None,
        ),
    )
    return history(db, task, limit=1)[0]


def dispatch(db, tasks, args):
    command = args.command
    task = getattr(args, "task", None)
    if task and task not in tasks:
        raise ValueError(f"未知任务: {task}")
    if command in {"start", "note", "block", "finish"}:
        db.execute("BEGIN IMMEDIATE")
        return write_event(db, task, command, args, progress(db, tasks)[task])
    db.execute("BEGIN")
    snapshot = progress(db, tasks)
    if command == "status":
        return list(snapshot.values())
    if command == "history":
        return history(db, task, args.query, args.limit)
    if command == "show":
        return {"task": snapshot[task], "recent": history(db, task)}
    active = [item for item in snapshot.values() if item["state"] == "active"]
    ready = [
        item
        for item in snapshot.values()
        if item["state"] == "pending" and not item["waiting"]
    ]
    blocked = [item for item in snapshot.values() if item["state"] == "blocked"]
    candidates = active or ready or blocked
    focus = candidates[0] if candidates else None
    return {
        "focus": focus,
        "active": active,
        "ready": ready,
        "blocked": blocked,
        "recent": history(db, focus["id"], limit=8) if focus else [],
    }


def print_result(args, result):
    if args.json or args.command not in {"status", "resume"}:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    if args.command == "status":
        for item in result:
            waiting = (
                f"（等待 {', '.join(item['waiting'])}）" if item["waiting"] else ""
            )
            state = (
                "ready" if item["state"] == "pending" and not waiting else item["state"]
            )
            print(f"{item['id']:9} {state:8} {item['title']}{waiting}")
        return
    focus = result["focus"]
    if focus:
        print(f"当前任务 {focus['id']} · {focus['state']} · {focus['title']}")
        if focus["next"]:
            print(f"续做动作  {focus['next']}")
    else:
        print("当前没有可续做的任务")
    for key, label in (
        ("active", "进行中"),
        ("ready", "可开始"),
        ("blocked", "已阻塞"),
    ):
        ids = ", ".join(item["id"] for item in result[key])
        print(f"{label}  {ids or '—'}")
    for event in result["recent"]:
        print(f"  {event['at']}  {event['action']}  {event['summary']}")
        for path in event["evidence"]:
            print(f"    {path}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument(
        "--repo", type=Path, default=ROOT, help="事件 HEAD 所属的检出目录"
    )
    parser.add_argument("--json", action="store_true")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("status", "resume"):
        commands.add_parser(name)
    commands.add_parser("show").add_argument("task")
    search = commands.add_parser("history")
    search.add_argument("task", nargs="?")
    search.add_argument("--query")
    search.add_argument("--limit", type=int, default=20)
    for name in ("start", "note", "block", "finish"):
        change = commands.add_parser(name)
        change.add_argument("task")
        change.add_argument("--summary")
        change.add_argument("--evidence", action="append", default=[])
        change.add_argument("--next")
    args = parser.parse_args()
    try:
        if getattr(args, "limit", 1) < 1:
            raise ValueError("--limit 必须大于零")
        path = args.db.expanduser()
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with closing(sqlite3.connect(path, timeout=10)) as db, db:
            db.row_factory = sqlite3.Row
            db.executescript(SCHEMA)
            result = dispatch(db, catalog(), args)
    except (OSError, ValueError, sqlite3.Error, subprocess.CalledProcessError) as exc:
        parser.exit(2, f"{exc}\n")
    print_result(args, result)


if __name__ == "__main__":
    main()
