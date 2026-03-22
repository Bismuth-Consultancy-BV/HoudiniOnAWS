import argparse
import os
import subprocess
import json
import sys
import logging

logger = logging.getLogger(__name__)

AURORA_TOOLING_ROOT = os.getenv("AURORA_TOOLING_ROOT", "")
if not AURORA_TOOLING_ROOT:
    raise ValueError("AURORA_TOOLING_ROOT environment variable is not set.")
if AURORA_TOOLING_ROOT not in sys.path:
    sys.path.insert(0, AURORA_TOOLING_ROOT)

from infra.utils.sesiweb_utils import get_houdini_download_info
from infra.utils.terraform_utils import (
    get_terraform_outputs,
    run_terraform_with_vars,
    initialize_terraform,
    terraform_destroy,
)
from infra.utils.packer_utils import run_packer_with_vars, initialize_packer
from infra.utils.misc_utils import require_admin
from infra.utils.constants import SIDEFX_SECRETS_NAME, GITHUB_CREDENTIALS_NAME
from infra.utils.constants import BATCH_AMI_NAME, SESSION_AMI_NAME
from infra.utils.aws_utils import get_aws_region

# All paths are resolved relative to this file's location so the project
# can be cloned anywhere without hard-coding machine-specific paths.
_INFRA_DIR = os.path.dirname(os.path.realpath(__file__))
_PROVISIONING_DIR = os.path.join(_INFRA_DIR, "provisioning")
_BUILDING_DIR = os.path.join(_PROVISIONING_DIR, "building")
_DEPLOYMENT_DIR = os.path.join(_PROVISIONING_DIR, "deployment")


def get_houdini_version_and_download_info(
    aws_region: str, include_download_info: bool = True
) -> dict:
    """
    Read Houdini version from houdini_version.json and optionally fetch download info.

    Args:
        aws_region: AWS region for API calls.
        include_download_info: Whether to fetch download info from SideFX API.

    Returns:
        dict containing version info and optionally download info.
    """
    json_version_file = os.path.join(
        _INFRA_DIR, "docker", "houdini", "install_files", "houdini_version.json"
    )

    with open(json_version_file, "r", encoding="utf-8") as f:
        config = json.load(f)

    houdini_major = config.get("houdini_major")
    houdini_minor = config.get("houdini_minor")
    houdini_build = config.get("houdini_build")
    eula_date = config.get("eula_date")
    python_version = config.get("python_version")
    houdini_version = f"{houdini_major}.{houdini_minor}.{houdini_build}"

    result = {
        "houdini_major": houdini_major,
        "houdini_minor": houdini_minor,
        "houdini_build": houdini_build,
        "houdini_version": houdini_version,
        "eula_date": eula_date,
        "python_version": python_version,
    }

    if include_download_info:
        download_info = get_houdini_download_info(
            houdini_version, aws_region, SIDEFX_SECRETS_NAME
        )
        result["download_info"] = download_info
        logger.info(f"Download info: {download_info['filename']}")

    return result


# ======================================================================
#  Command handlers — each corresponds to a CLI flag
# ======================================================================


def cmd_destroy_all(aws_region: str) -> None:
    """Tear down all Terraform-managed resources."""
    require_admin()
    logger.info("Destroying all resources...")

    targets = [
        ("building", {"github_credentials_name": GITHUB_CREDENTIALS_NAME}, None),
        ("deployment/batch", {}, BATCH_AMI_NAME),
        ("deployment/session", {}, SESSION_AMI_NAME),
    ]

    for subdir, extra_vars, ami_name in targets:
        build_dir = os.path.normpath(os.path.join(_PROVISIONING_DIR, subdir))
        logger.info(f"Destroying resources in {build_dir}...")

        tf_vars = {
            "aws_region": aws_region,
            "sidefx_oauth_credentials_name": SIDEFX_SECRETS_NAME,
            **extra_vars,
        }
        if ami_name:
            tf_vars["aurora_ami_name"] = ami_name

        terraform_destroy(tf_vars, build_dir)


def cmd_build_images(aws_region: str) -> None:
    """Build all Docker images defined in ``_INFRA_DIR/docker/``."""
    images = {"houdini": "houdini_aws:latest"}

    for dir_name, image_name in images.items():
        build_dir = os.path.join(_INFRA_DIR, "docker", dir_name)
        logger.info(f"Building {image_name} from {build_dir}...")

        try:
            houdini_info = get_houdini_version_and_download_info(
                aws_region, include_download_info=True
            )
        except (FileNotFoundError, json.JSONDecodeError, KeyError) as e:
            logger.warning(f"Skipping {image_name} build: {e}")
            continue
        except Exception as e:
            raise RuntimeError(f"Failed to get download info: {e}")

        download_info = houdini_info["download_info"]
        cmd = [
            "docker",
            "build",
            "-t",
            image_name,
            build_dir,
            "--build-arg",
            "SDL_VIDEODRIVER=dummy",
            "--build-arg",
            f"HOUDINI_MAJOR={houdini_info['houdini_major']}",
            "--build-arg",
            f"HOUDINI_MINOR={houdini_info['houdini_minor']}",
            "--build-arg",
            f"PYTHON_VERSION={houdini_info['python_version']}",
            "--build-arg",
            f"DOWNLOAD_URL={download_info['download_url']}",
            "--build-arg",
            f"DOWNLOAD_FILENAME={download_info['filename']}",
            "--build-arg",
            f"DOWNLOAD_HASH={download_info['hash']}",
            "--build-arg",
            f"EULA_DATE={houdini_info['eula_date']}",
        ]

        if not os.path.isdir(build_dir):
            logger.warning(f"Directory {build_dir} not found, skipping...")
            continue

        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            universal_newlines=True,
            encoding="utf-8",
        )
        for line in iter(process.stdout.readline, ""):
            print(line, end="", flush=True)
        process.wait()

        if process.returncode != 0:
            raise RuntimeError(f"Failed to build {image_name} from {build_dir}")

    logger.info("All images built successfully.")


def cmd_build_ami(aws_region: str, keypair: str, is_session: bool = False) -> None:
    """Build an AMI via Packer (batch or session)."""
    require_admin()

    if not os.path.isfile(keypair):
        raise ValueError(f"Keypair file {keypair} not found.")

    # Provision build infrastructure (VPC, SG, etc.)
    initialize_terraform(_BUILDING_DIR)

    tf_vars = {
        "aws_region": aws_region,
        "sidefx_oauth_credentials_name": SIDEFX_SECRETS_NAME,
        "github_credentials_name": GITHUB_CREDENTIALS_NAME,
    }
    run_terraform_with_vars(tf_vars, _BUILDING_DIR)
    tf_outputs = get_terraform_outputs(_BUILDING_DIR)

    # Select the correct Packer template
    pkr_file_name = (
        "provision_session_ami.pkr.hcl" if is_session else "provision_batch_ami.pkr.hcl"
    )
    ami_name = SESSION_AMI_NAME if is_session else BATCH_AMI_NAME
    hcl_file = os.path.abspath(os.path.join(_BUILDING_DIR, pkr_file_name))

    packer_vars = {
        "vpc_id": tf_outputs["vpc_id"],
        "subnet_id": tf_outputs["subnet_id"],
        "security_group_id": tf_outputs["security_group_id"],
        "provisioning_iam_role": tf_outputs["provisioning_iam_role"],
        "keypair_name": "aurora-key-pair",
        "aws_region": aws_region,
        "keypair_path": keypair,
        "ami_name": ami_name,
    }

    # Session AMIs need Houdini download info baked in
    if is_session:
        try:
            houdini_info = get_houdini_version_and_download_info(
                aws_region, include_download_info=True
            )
            download_info = houdini_info["download_info"]
            packer_vars["houdini_download_url"] = download_info["download_url"]
            packer_vars["houdini_download_filename"] = download_info["filename"]
            packer_vars["houdini_download_hash"] = download_info["hash"]
        except Exception as e:
            raise RuntimeError(
                f"Failed to get Houdini download info for AMI build: {e}"
            )

    initialize_packer(hcl_file)
    run_packer_with_vars(packer_vars, hcl_file)


def _provision_terraform_deployment(
    subdir: str,
    aws_region: str,
    ami_name: str,
    extra_vars: dict = None,
) -> dict:
    """
    Run Terraform init + apply for a deployment subdirectory and write
    the outputs to ``samples/tf_outputs.json``.

    Returns:
        The Terraform outputs dict.
    """
    require_admin()
    build_dir = os.path.abspath(
        os.path.join(_DEPLOYMENT_DIR, subdir) if subdir else _DEPLOYMENT_DIR
    )

    initialize_terraform(build_dir)

    tf_vars = {
        "aws_region": aws_region,
        "aurora_ami_name": ami_name,
        "sidefx_oauth_credentials_name": SIDEFX_SECRETS_NAME,
        **(extra_vars or {}),
    }
    run_terraform_with_vars(tf_vars, build_dir)

    tf_outputs = get_terraform_outputs(build_dir)
    outputs_path = os.path.join(AURORA_TOOLING_ROOT, "samples", "tf_outputs.json")
    with open(outputs_path, "w", encoding="utf-8") as f:
        json.dump(tf_outputs, f, indent=4)

    return tf_outputs


def cmd_provision_batch(aws_region: str) -> None:
    """Provision batch-mode AWS infrastructure."""
    _provision_terraform_deployment("batch", aws_region, BATCH_AMI_NAME)


def cmd_provision_session(aws_region: str) -> None:
    """Provision session-mode AWS infrastructure."""
    _provision_terraform_deployment(
        "",  # session uses the deployment root
        aws_region,
        SESSION_AMI_NAME,
        extra_vars={"enable_session_mode": "true"},
    )


# ======================================================================
#  CLI
# ======================================================================


def main(input_args: argparse.Namespace) -> None:
    aws_region = get_aws_region()

    if input_args.destroy_all:
        cmd_destroy_all(aws_region)
        return

    if input_args.build_images:
        cmd_build_images(aws_region)

    if input_args.build_ami:
        cmd_build_ami(
            aws_region,
            input_args.keypair,
            is_session=input_args.provision_service_aws,
        )

    if input_args.provision_batch_aws:
        cmd_provision_batch(aws_region)

    if input_args.provision_service_aws:
        cmd_provision_session(aws_region)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()

    build_ami = parser.add_argument(
        "--build_ami",
        help="Build AWS AMI. Requires admin privileges.",
        action="store_true",
    )
    provision_batch_aws = parser.add_argument(
        "--provision_batch_aws",
        help="Provision AWS batch infrastructure. Requires admin privileges.",
        action="store_true",
    )
    provision_service_aws = parser.add_argument(
        "--provision_service_aws",
        help="Provision AWS service infrastructure. Requires admin privileges.",
        action="store_true",
    )
    aws_keypair = parser.add_argument(
        "--keypair",
        help="Keypair required for provisioning AWS infrastructure and AMI.",
        default=os.path.join(_PROVISIONING_DIR, "aurora-key-pair.pem"),
    )
    build_images = parser.add_argument(
        "--build_images",
        help="Build Docker images.",
        action="store_true",
    )
    parser.add_argument(
        "--destroy_all",
        help="Destroy all resources.",
        action="store_true",
    )
    args = parser.parse_args()
    main(args)
