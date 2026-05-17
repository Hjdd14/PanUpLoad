"""PanUpLoad — Multi-cloud disk backup tool.

Usage:
    python main.py
"""

import multiprocessing
import os
import sys
import flet as ft

from panupdate.ui.app import PanUpLoadApp


def main():
    multiprocessing.freeze_support()

    # Store data in user's app data directory
    data_dir = os.path.join(
        os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
        "PanUpLoad",
    )

    app = PanUpLoadApp(data_dir)
    ft.run(main=app.run, name="PanUpLoad")


if __name__ == "__main__":
    main()
