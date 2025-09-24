from typing import Dict
import subprocess
import os


def check_packer_installed() -> None:
    """Check if Packer is installed."""
    try:
        subprocess.run(
            ["packer", "-v"],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        raise EnvironmentError("Packer is not installed or not found in PATH.")


def initialize_packer(hcl_path: str) -> None:
    """Initialize Packer in the specified directory."""
    check_packer_installed()
    subprocess.run(
        [
            "packer",
            "init",
            hcl_path,
        ],
        check=True,
        cwd=os.path.dirname(hcl_path),
    )


def run_packer_with_vars(vars: Dict[str, str], hcl_file: str) -> None:
    """Run Packer with the specified variables."""

    # These ensure we do not time out during the build process
    # They also ensure that the Packer build process is verbose
    os.environ["PACKER_LOG"] = "1"
    os.environ["AWS_MAX_ATTEMPTS"] = "1000"
    os.environ["AWS_POLL_DELAY_SECONDS"] = "30"

    check_packer_installed()

    packer_cmd = [
        "packer",
        "build",
    ]
    for key, value in vars.items():
        packer_cmd.append(f"-var={key}={value}")

    packer_cmd.append(hcl_file)

    subprocess.run(
        packer_cmd,
        check=True,
        cwd=os.path.dirname(hcl_file),
    )
