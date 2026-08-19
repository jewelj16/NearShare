"""UI utilities for displaying the NearShare logo."""

import sys

ORANGE = "\033[38;5;214m"
WHITE = "\033[97m"
RESET = "\033[0m"
DIM = "\033[2m"

def print_logo(mode: str) -> None:
    """Print the NearShare ASCII logo with ANSI colors."""
    lines = [
        (" _   _                 ", "_____ _"),
        ("| \ | |               ", "/ ____| |"),
        ("|  \| | ___  __ _ _ _ ", "| (___ | |__   __ _ _ __ ___"),
        ("| . ` |/ _ \/ _` | '__", "\___ \| '_ \ / _` | '__/ _ \\"),
        ("| |\  |  __/ (_| | |  ", "____) | | | | (_| | | |  __/"),
        ("|_| \_|\___|\__,_|_| ", "|_____/|_| |_|\__,_|_|  \___|"),
    ]
    
    print()
    for near, share in lines:
        print(f"{ORANGE}{near}{WHITE}{share}{RESET}")
    
    # Print the mode centered-ish under the logo
    mode_str = f"--- {mode.upper()} ---"
    print(f"{DIM}{mode_str:^50}{RESET}")
    print()
