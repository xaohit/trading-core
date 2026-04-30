#!/usr/bin/env python3
"""
Trading Core — 进程管理器
统一管理 daemon (扫描+监控) 和 web 服务
用法:
    python server.py start     # 启动全部
    python server.py stop      # 停止全部
    python server.py restart   # 重启
    python server.py status    # 查看状态
    python server.py logs      # 查看日志
"""
import sys
import os
import signal
import time
import subprocess
import argparse
import json
from pathlib import Path
from datetime import datetime, timezone, timedelta

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

TZ_UTC8 = timezone(timedelta(hours=8))
BASE_DIR = Path(__file__).parent
PID_DIR = BASE_DIR / ".pids"
PID_DIR.mkdir(exist_ok=True)

DAEMON_PID_FILE = PID_DIR / "daemon.pid"
WEB_PID_FILE = PID_DIR / "web.pid"
LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)


def popen_kwargs(log_file):
    kwargs = {
        "cwd": str(BASE_DIR),
        "stdout": log_file,
        "stderr": subprocess.STDOUT,
    }
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["preexec_fn"] = os.setsid
    return kwargs


def log_path(name):
    return LOG_DIR / f"{name}.log"


def read_pid(f):
    if f.exists():
        try:
            return int(f.read_text().strip())
        except:
            return None
    return None


def is_running(pid):
    if not pid:
        return False
    if os.name == "nt":
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}"],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace",
        )
        return str(pid) in result.stdout
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def status():
    daemon_pid = read_pid(DAEMON_PID_FILE)
    web_pid = read_pid(WEB_PID_FILE)

    daemon_ok = is_running(daemon_pid)
    web_ok = is_running(web_pid)

    print(f"Trading Core 状态 ({datetime.now(TZ_UTC8).strftime('%m-%d %H:%M')})")
    print(f"  Daemon : {'🟢 运行中' if daemon_ok else '⚫ 停止'} (PID {daemon_pid or '-'})")
    print(f"  Web UI : {'🟢 运行中' if web_ok else '⚫ 停止'} (PID {web_pid or '-'})")

    if daemon_ok or web_ok:
        print("\n  访问: http://localhost:8080")

    return daemon_ok and web_ok


def kill_pid(pid, name, timeout=5):
    if not pid or not is_running(pid):
        return True
    try:
        os.kill(pid, signal.SIGTERM)
        # 等待进程退出
        start = time.time()
        while is_running(pid) and time.time() - start < timeout:
            time.sleep(0.2)
        if is_running(pid):
            os.kill(pid, signal.SIGKILL)
        print(f"  {name} 已停止 (PID {pid})")
        return True
    except OSError as e:
        print(f"  {name} 停止失败: {e}")
        return False


def start_daemon():
    pid = read_pid(DAEMON_PID_FILE)
    if is_running(pid):
        print(f"  Daemon 已在运行 (PID {pid})")
        return False

    log_file = open(log_path("daemon"), "a")
    p = subprocess.Popen(
        [sys.executable, str(BASE_DIR / "main.py")],
        **popen_kwargs(log_file),
    )
    DAEMON_PID_FILE.write_text(str(p.pid))
    print(f"  Daemon 启动 (PID {p.pid})")
    return True


def start_web():
    pid = read_pid(WEB_PID_FILE)
    if is_running(pid):
        print(f"  Web UI 已在运行 (PID {pid})")
        return False

    log_file = open(log_path("web"), "a")
    p = subprocess.Popen(
        [sys.executable, str(BASE_DIR / "web.py")],
        **popen_kwargs(log_file),
    )
    WEB_PID_FILE.write_text(str(p.pid))
    print(f"  Web UI 启动 (PID {p.pid})")
    return True


def stop_all():
    print("正在停止...")
    daemon_pid = read_pid(DAEMON_PID_FILE)
    web_pid = read_pid(WEB_PID_FILE)

    kill_pid(daemon_pid, "Daemon")
    kill_pid(web_pid, "Web UI")

    # 清理 PID 文件
    for f in [DAEMON_PID_FILE, WEB_PID_FILE]:
        if f.exists():
            f.unlink()

    print("全部已停止")


def start_all():
    print("启动 Trading Core...")
    init_db()
    start_web()
    time.sleep(1)
    start_daemon()
    time.sleep(1)
    status()


def restart_all():
    stop_all()
    time.sleep(2)
    start_all()


def logs(name=None, lines=50):
    for log_name in ["daemon", "web"]:
        if name and name != log_name:
            continue
        f = log_path(log_name)
        if f.exists():
            print(f"\n=== {log_name.upper()} 日志 (末{lines}行) ===")
            content = f.read_text()
            log_lines = content.strip().splitlines()
            for line in log_lines[-lines:]:
                print(line)
        else:
            print(f"\n=== {log_name.upper()} 日志 (无) ===")


def tail_logs():
    """实时跟踪日志"""
    print("实时日志 (Ctrl+C 退出)")
    import select

    files = {
        "daemon": open(log_path("daemon")),
        "web": open(log_path("web")),
    }

    def tail_file(name, f):
        try:
            while True:
                line = f.readline()
                if line:
                    print(f"[{name}] {line}", end="")
                else:
                    time.sleep(0.5)
        except:
            pass

    import threading
    for name, f in files.items():
        t = threading.Thread(target=tail_file, args=(name, f), daemon=True)
        t.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n停止跟踪")


def init_db():
    sys.path.insert(0, str(BASE_DIR))
    from db.connection import init_db as _init
    _init()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Trading Core 进程管理")
    parser.add_argument("action", choices=["start", "stop", "restart", "status", "logs", "tail"])
    parser.add_argument("--name", choices=["daemon", "web"], help="指定服务")
    parser.add_argument("--lines", type=int, default=50, help="日志行数")

    args = parser.parse_args()

    if args.action == "start":
        start_all()
    elif args.action == "stop":
        stop_all()
    elif args.action == "restart":
        restart_all()
    elif args.action == "status":
        status()
    elif args.action == "logs":
        logs(args.name, args.lines)
    elif args.action == "tail":
        tail_logs()
