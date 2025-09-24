import argparse
import os
import subprocess
import json
import sys


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
from infra.utils.constants import AMI_NAME
from infra.utils.aws_utils import get_aws_region


def main(args):
    # Define directories and image names
    images = {
        "houdini": "houdini_aws:latest",
    }
    aws_region = get_aws_region()

    if args.destroy_all:
        # Make sure the script is running with admin privileges
        require_admin()
        print("Destroying all resources...")

        for dir_name in ["building", "deployment"]:
            build_dir = os.path.join(
                os.path.dirname(os.path.realpath(__file__)), "provisioning", dir_name
            )
            print(f"Destroying resources in {build_dir}...")
            terraform_vars = {
                "aws_region": aws_region,
                "sidefx_oauth_credentials_name": SIDEFX_SECRETS_NAME
            }
            if dir_name == "building":
                terraform_vars["github_credentials_name"] = GITHUB_CREDENTIALS_NAME

            if dir_name == "deployment":
                terraform_vars["aurora_ami_name"] = AMI_NAME
            terraform_destroy(terraform_vars, build_dir)

        return
    if args.build_images:
        # Iterate over the directories and build images
        for dir_name, image_name in images.items():
            build_dir = os.path.join(
                os.path.dirname(os.path.realpath(__file__)), "docker", dir_name
            )
            print(f"Building {image_name} from {build_dir}...")

            json_version_file = os.path.join(
                build_dir, "install_files", "houdini_version.json"
            )
            try:
                with open(json_version_file, "r", encoding="utf-8") as f:
                    config = json.load(f)

                houdini_major = config.get("houdini_major")
                houdini_minor = config.get("houdini_minor")
                houdini_build = config.get("houdini_build")
                eula_date = config.get("eula_date")
                python_version = config.get("python_version")
                houdini_version = f"{houdini_major}.{houdini_minor}.{houdini_build}"

            except FileNotFoundError:
                print(
                    f"Version file {json_version_file} not found, skipping {image_name} build."
                )
                continue
            except json.JSONDecodeError:
                print(
                    f"Error decoding JSON from {json_version_file}, skipping {image_name} build."
                )
                continue
            except KeyError as e:
                print(
                    f"Missing key in JSON file {json_version_file}: {e}, skipping {image_name} build."
                )
                continue

            # Get download info from SideFX API
            try:
                download_info = get_houdini_download_info(
                    houdini_version, aws_region, SIDEFX_SECRETS_NAME
                )
                print(f"Download info: {download_info['filename']}")
            except Exception as e:
                raise RuntimeError(f"Failed to get download info: {e}")

            cmd = [
                "docker",
                "build",
                "-t",
                image_name,
                build_dir,
                "--build-arg",
                "SDL_VIDEODRIVER=dummy",
                "--build-arg",
                f"HOUDINI_MAJOR={houdini_major}",
                "--build-arg",
                f"HOUDINI_MINOR={houdini_minor}",
                "--build-arg",
                f"PYTHON_VERSION={python_version}",
                "--build-arg",
                f"DOWNLOAD_URL={download_info['download_url']}",
                "--build-arg",
                f"DOWNLOAD_FILENAME={download_info['filename']}",
                "--build-arg",
                f"DOWNLOAD_HASH={download_info['hash']}",
                "--build-arg",
                f"EULA_DATE={eula_date}",
            ]
            if os.path.isdir(build_dir):
                process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                    universal_newlines=True,
                    encoding="utf-8",
                )

                # Print output live
                for line in iter(process.stdout.readline, ""):
                    print(line, end="", flush=True)

                process.wait()

                if process.returncode != 0:
                    print(f"Failed to build {image_name} from {build_dir}")
                    exit(1)
            else:
                print(f"Directory {build_dir} not found, skipping...")

        print("All images built successfully.")

    if args.build_ami:

        # Make sure the script is running with admin privileges
        require_admin()

        if not os.path.isfile(args.keypair):
            raise ValueError(f"Keypair file {args.keypair} not found.")

        tf_file = os.path.join(
            os.path.dirname(os.path.realpath(__file__)),
            "provisioning",
            "provision_ami.pkr.hcl",
        )
        tf_file = os.path.abspath(tf_file)

        build_dir = os.path.abspath(
            os.path.join(
                os.path.dirname(os.path.realpath(__file__)), "provisioning", "building"
            )
        )

        # Provision the AWS infrastructure
        initialize_terraform(build_dir)

        terraform_vars = {
            "aws_region": aws_region,
            "sidefx_oauth_credentials_name": SIDEFX_SECRETS_NAME,
            "github_credentials_name": GITHUB_CREDENTIALS_NAME
        }
        run_terraform_with_vars(
            terraform_vars,
            build_dir,
        )

        tf_outputs = get_terraform_outputs(build_dir)

        hcl_file = os.path.join(
            os.path.dirname(os.path.realpath(__file__)),
            "provisioning",
            "building",
            "provision_ami.pkr.hcl",
        )
        hcl_file = os.path.abspath(hcl_file)

        packer_vars = {
            "vpc_id": tf_outputs["vpc_id"],
            "subnet_id": tf_outputs["subnet_id"],
            "security_group_id": tf_outputs["security_group_id"],
            "provisioning_iam_role": tf_outputs["provisioning_iam_role"],
            "keypair_name": "aurora-key-pair",
            "aws_region": aws_region,
            "keypair_path": args.keypair,
            "ami_name": AMI_NAME,
        }

        initialize_packer(hcl_file)

        run_packer_with_vars(
            packer_vars,
            hcl_file,
        )

    if args.provision_aws:
        # Make sure the script is running with admin privileges
        require_admin()

        build_dir = os.path.abspath(
            os.path.join(
                os.path.dirname(os.path.realpath(__file__)),
                "provisioning",
                "deployment",
            )
        )

        # Provision the AWS infrastructure
        initialize_terraform(build_dir)

        terraform_vars = {
            "aws_region": aws_region,
            "aurora_ami_name": AMI_NAME,
            "sidefx_oauth_credentials_name": SIDEFX_SECRETS_NAME,
        }
        run_terraform_with_vars(
            terraform_vars,
            build_dir,
        )

        tf_outputs = get_terraform_outputs(build_dir)
        with open(
            os.path.join(AURORA_TOOLING_ROOT, "samples", "tf_outputs.json"),
            "w",
            encoding="utf-8",
        ) as f:
            json.dump(tf_outputs, f, indent=4)


if __name__ == "__main__":
    argparse = argparse.ArgumentParser()

    build_ami = argparse.add_argument(
        "--build_ami",
        help="Build AWS AMI. Requires admin privileges.",
        action="store_true",
    )
    provision_aws = argparse.add_argument(
        "--provision_aws",
        help="Provision AWS infrastructure. Requires admin privileges.",
        action="store_true",
    )
    aws_keypair = argparse.add_argument(
        "--keypair",
        help="Keypair required for provisioning AWS infrastructure and AMI.",
        default="H:/Github/HoudiniOnAWS/infra/provisioning/aurora-key-pair.pem",
    )
    build_images = argparse.add_argument(
        "--build_images",
        help="Build Docker images.",
        action="store_true",
    )
    argparse.add_argument(
        "--destroy_all",
        help="Destroy all resources.",
        action="store_true",
    )
    args = argparse.parse_args()
    main(args)
