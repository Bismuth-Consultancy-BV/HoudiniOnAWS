import os
import ctypes
import sys
import shutil
import contextlib


def require_admin() -> None:
    """Ensure the script is running with administrator privileges."""
    try:
        is_admin = os.getuid() == 0  # Linux/macOS
    except AttributeError:
        try:
            is_admin = ctypes.windll.shell32.IsUserAnAdmin() != 0  # Windows
        except:
            is_admin = False

    if not is_admin:
        print("Error: This script requires administrator privileges.")
        print("Please run as administrator (Windows) or with sudo (Unix/Linux/macOS).")
        sys.exit(1)


@contextlib.contextmanager
def credentials_root_context(root_dir):
    """Create a temporary directory for Houdini credentials."""
    os.makedirs(root_dir, exist_ok=True)
    try:
        yield root_dir
    finally:
        shutil.rmtree(root_dir, ignore_errors=True)
