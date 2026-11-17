#!/usr/bin/env python3
"""
Start the approach services and keep watch over them.

Each service runs as its own process. If any one of them stops, the others
are stopped too and this script exits with an error, so Docker's restart
policy brings the whole container back. Without that, a crashed service
would leave the container "Up" while part of its output went stale.
"""
import signal
import subprocess
import sys
import time

# METAR collection, approach/category status, and the TAF forecast.
SCRIPTS = ["approach_service.py", "approach_status.py", "taf_decoder.py",
           "trend_status_service.py"]


def main():
    processes = {script: subprocess.Popen([sys.executable, script])
                 for script in SCRIPTS}

    def stop_all(*_):
        for p in processes.values():
            if p.poll() is None:
                p.terminate()

    # `docker stop` sends SIGTERM: pass it on and leave quietly.
    signal.signal(signal.SIGTERM, lambda *a: (stop_all(), sys.exit(0)))

    while True:
        for script, p in processes.items():
            code = p.poll()
            if code is not None:
                print(f"run_services: {script} exited with code {code}; "
                      f"stopping the others so the container restarts",
                      flush=True)
                stop_all()
                for other in processes.values():
                    try:
                        other.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        other.kill()
                sys.exit(1)
        time.sleep(5)


if __name__ == "__main__":
    main()
