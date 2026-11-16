#!/usr/bin/env python3
import multiprocessing
import sys

def run_script(script):
    import subprocess
    subprocess.run([sys.executable, script])

if __name__ == "__main__":
    # TAF service removed - unreliable and timing out on aviationweather.gov
    scripts = ["approach_service.py", "approach_status.py", "trend_status_service.py"]
    processes = []
    for script in scripts:
        p = multiprocessing.Process(target=run_script, args=(script,))
        p.start()
        processes.append(p)

    for p in processes:
        p.join()

