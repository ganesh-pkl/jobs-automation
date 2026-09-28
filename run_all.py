"""
Sequential Job Application Runner

This script runs all three job portal bots sequentially.
Running them sequentially prevents log file corruption and ensures
that daily application limits (tracked in applications_log.csv) are respected.

Usage:
    python run_all.py
"""
import subprocess
import sys
import time

def run_script(script_name):
    print(f"\n{'='*50}")
    print(f"Starting {script_name}...")
    print(f"{'='*50}\n")
    
    try:
        # We use sys.executable to ensure it uses the exact same python interpreter (e.g. .venv)
        result = subprocess.run([sys.executable, script_name])
        if result.returncode != 0:
            print(f"\n[!] {script_name} exited with error code {result.returncode}")
        else:
            print(f"\n[+] {script_name} completed successfully.")
    except KeyboardInterrupt:
        print(f"\n[!] Run cancelled by user during {script_name}.")
        sys.exit(130)
    except Exception as e:
        print(f"\n[!] Failed to run {script_name}: {e}")

def main():
    from daily_pipeline import run_pipeline
    run_pipeline()

if __name__ == "__main__":
    main()
