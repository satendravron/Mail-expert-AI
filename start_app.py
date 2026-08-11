"""
One-command launcher for Mail Expert AI.

Starts BOTH the web server (uvicorn) and the reminder scheduler as background
processes from a single terminal, and opens your browser to the dashboard.
Gmail fetching is deliberately NOT auto-started here since it's something
you trigger on demand (run `python gmail_connector.py` whenever you want
fresh mail) rather than something that should run continuously.

Run:
    python start_app.py
Stop:
    Ctrl+C   (this cleanly shuts down both background processes)
"""

import subprocess
import sys
import time
import webbrowser

PROCESSES = []


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
    """Interleaves output from both processes with a [name] prefix so you can
    still see what's happening, same as running them in separate terminals."""
    import threading

    def _pump(name, proc):
        for line in proc.stdout:
            print(f"[{name}] {line.rstrip()}")

    threads = [threading.Thread(target=_pump, args=(name, proc), daemon=True)
               for name, proc in PROCESSES]
    for t in threads:
        t.start()


def main():
    api_proc = start([sys.executable, "-m", "uvicorn", "api:app", "--reload"], "server")
    scheduler_proc = start([sys.executable, "reminder_scheduler.py"], "scheduler")

    stream_output()

    print("\nGiving the server a moment to start...")
    time.sleep(3)
    print("Opening dashboard at http://127.0.0.1:8000/ ...")
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
