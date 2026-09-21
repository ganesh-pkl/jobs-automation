"""Terminal input with a timeout; no background readers can steal later answers."""
import os
import sys
import time


def ask_user(prompt: str, timeout_seconds: int = 120) -> str | None:
    print(f"\nNEEDS YOUR INPUT ({timeout_seconds}s; blank skips)\n{prompt}\n> ", end='', flush=True)
    if not sys.stdin.isatty():
        print("Interactive terminal required; skipping.")
        return None
    deadline = time.monotonic() + timeout_seconds
    if os.name == 'nt':
        import msvcrt
        chars = []
        while time.monotonic() < deadline:
            if not msvcrt.kbhit():
                time.sleep(0.05)
                continue
            char = msvcrt.getwch()
            if char == '\x03':
                raise KeyboardInterrupt
            if char in ('\r', '\n'):
                print()
                return ''.join(chars).strip() or None
            if char in ('\x00', '\xe0'):
                msvcrt.getwch()
            elif char == '\b':
                if chars:
                    chars.pop()
                    print('\b \b', end='', flush=True)
            else:
                chars.append(char)
                print(char, end='', flush=True)
    else:
        import select
        import termios
        ready, _, _ = select.select([sys.stdin], [], [], max(0, deadline - time.monotonic()))
        if ready:
            return sys.stdin.readline().strip() or None
        termios.tcflush(sys.stdin, termios.TCIFLUSH)
    print("\nNo response in time; skipping.")
    return None
