"""
One-command launcher for Mail Expert AI.

Starts BOTH the web server (uvicorn) bound to all network interfaces (0.0.0.0)
and the reminder scheduler as background processes from a single terminal.

Run:
    python start_app.py
Stop:
    Ctrl+C   (this cleanly shuts down both background processes)
"""

import subprocess
import sys
import time
import socket
import webbrowser

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

PROCESSES = []


def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def start(cmd: list, name: str):
    print(f"Starting {name}...")
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    PROCESSES.append((name, proc))
    return proc


def stream_output():
    import threading

    def _pump(name, proc):
        for line in proc.stdout:
            print(f"[{name}] {line.rstrip()}")

    threads = [threading.Thread(target=_pump, args=(name, proc), daemon=True)
               for name, proc in PROCESSES]
    for t in threads:
        t.start()


def main():
    local_ip = get_local_ip()

    api_proc = start([sys.executable, "-m", "uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8000", "--reload"], "server")
    scheduler_proc = start([sys.executable, "reminder_scheduler.py"], "scheduler")

    stream_output()

    print("\nGiving the server a moment to start...")
    time.sleep(3)

    print("=" * 65)
    print(f"📬 MAIL EXPERT AI IS LIVE AND READY!")
    print(f"  • Local Laptop URL : http://127.0.0.1:8000/")
    print(f"  • Mobile Phone URL : http://{local_ip}:8000/")
    print("=" * 65)

    webbrowser.open("http://127.0.0.1:8000/")

    print("\nBoth processes running. Press Ctrl+C to stop everything.\n")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nShutting down...")
        for name, proc in PROCESSES:
            proc.terminate()
        for name, proc in PROCESSES:
            proc.wait()
        print("Stopped.")


if __name__ == "__main__":
    main()
