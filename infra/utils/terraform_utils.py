import json
import subprocess
from typing import Dict


def check_terraform_installed() -> None:
    """Check if Terraform is installed."""
    try:
        subprocess.run(
            ["terraform", "-version"],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        raise EnvironmentError("Terraform is not installed or not found in PATH.")


def initialize_terraform(build_dir: str) -> None:
    """Initialize Terraform in the specified directory."""
    check_terraform_installed()
    subprocess.run(
        [
            "terraform",
            "init",
        ],
        check=True,
        cwd=build_dir,
    )


def run_terraform_with_vars(vars: Dict[str, str], build_dir: str) -> None:
    """Run a Terraform command in the specified directory."""
    check_terraform_installed()
    terraform_cmd = [
        "terraform",
        "apply",
    ]
    for key, value in vars.items():
        terraform_cmd.append(f"-var={key}={value}")

    terraform_cmd.append("-auto-approve")
    subprocess.run(
        terraform_cmd,
        check=True,
        cwd=build_dir,
    )


def get_terraform_outputs(tf_dir):
    """Get Terraform outputs as a Python dict."""
    check_terraform_installed()
    result = subprocess.run(
        ["terraform", "output", "-json"],
        cwd=tf_dir,
        check=True,
        capture_output=True,
        text=True,
    )
    outputs = json.loads(result.stdout)
    return {k: v["value"] for k, v in outputs.items()}


def terraform_destroy(vars: Dict[str, str], build_dir: str) -> None:
    """Destroy Terraform-managed infrastructure in the specified directory."""
    check_terraform_installed()
    terraform_cmd = [
        "terraform",
        "destroy",
        "-auto-approve",
    ]
    for key, value in vars.items():
        terraform_cmd.append(f"-var={key}={value}")
    subprocess.run(
        terraform_cmd,
        check=True,
        cwd=build_dir,
    )
