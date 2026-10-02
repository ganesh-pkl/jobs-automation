"""
Multi-portal Job Application Runner (Naukri, LinkedIn, Hirist, Uplers, Instahyre, Foundit, Wellfound, Glassdoor/Indeed)

Runs application bots sequentially or concurrently in parallel.
Prevents duplicate applications and enforces daily application safety limits.

Usage:
    python run_all.py            # runs parallel across all active portals
    python run_all.py --serial   # runs sequentially one portal at a time
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
    import argparse
    parser = argparse.ArgumentParser(description="Multi-portal Job Application Pipeline Runner")
    parser.add_argument("--serial", action="store_true", help="Run sequentially one portal at a time")
    parser.add_argument("--parallel", action="store_true", default=True, help="Run all 8 portals in parallel (default)")
    args = parser.parse_args()

    from daily_pipeline import run_pipeline, run_parallel
    if args.serial:
        run_pipeline()
    else:
        run_parallel()

if __name__ == "__main__":
    main()
