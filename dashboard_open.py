#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""Open the authenticated local dashboard in the user's default browser."""
from pathlib import Path
import sys
import webbrowser


def main():
    try:
        token = Path("/run/gpu-lab-dashboard/token").read_text().strip()
    except (FileNotFoundError, PermissionError):
        raise SystemExit("GPU Lab dashboard is not available for this account. Check systemctl status gpu-lab-dashboard.service")
    if not webbrowser.open("http://127.0.0.1:8766/#token="+token, new=2):
        raise SystemExit("Could not open the default browser.")


if __name__ == "__main__":
    main()
