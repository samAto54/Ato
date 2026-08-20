import time
import sys
import random

CYAN = "\033[1;36m"
GREEN = "\033[1;32m"
WHITE = "\033[1;37m"
RESET = "\033[0m"


def simulate_matrix_scan(label, duration=0.5):
    end = time.time() + duration

    while time.time() < end:
        hex_dump = "".join(random.choice("0123456789ABCDEF") for _ in range(10))
        sys.stdout.write(f"\r{CYAN}[ATO]{RESET} {label}... 0x{hex_dump}")
        sys.stdout.flush()
        time.sleep(0.03)

    sys.stdout.write(f"\r{GREEN}[✓]{RESET} {label} COMPLETE\n")
    sys.stdout.flush()


def show_banner():
    print("\033[H\033[J", end="")

    print(f"{CYAN}====================================")
    print("                ATO")
    print("        CORE INTERFACE v0.1")
    print(f"===================================={RESET}\n")

    modules = [
        "INITIALIZING CORE SYSTEM",
        "LOADING USER INTERFACE",
        "SYNCING LOCAL ENVIRONMENT",
        "READY"
    ]

    for m in modules:
        simulate_matrix_scan(m)

    print(f"\n{GREEN}ATO ONLINE{RESET}\n")