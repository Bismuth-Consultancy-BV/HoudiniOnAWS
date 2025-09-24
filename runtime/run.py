import argparse
import json
import os
import sys
import time
from typing import Dict, Any

AURORA_TOOLING_ROOT = os.getenv("AURORA_TOOLING_ROOT", "")
if not AURORA_TOOLING_ROOT:
    raise ValueError("AURORA_TOOLING_ROOT environment variable is not set.")
if AURORA_TOOLING_ROOT not in sys.path:
    sys.path.insert(0, AURORA_TOOLING_ROOT)


from runtime.docker import utils as docker_utils
from infra.utils.aws_utils import get_aws_secrets
from infra.utils.aws_utils import get_aws_region
from infra.utils.constants import SIDEFX_SECRETS_NAME
from infra.utils.misc_utils import credentials_root_context

DATA_ROOT = os.path.join(AURORA_TOOLING_ROOT, "SHARED")
DEFAULT_MOUNT_PATHS = {
    AURORA_TOOLING_ROOT: "/mnt/tooling/",
    DATA_ROOT: "/mnt/data/",
}


def generate_houdini_content(in_args: Any, timings_dict: Dict[str, float]):
    """Generate Houdini content based on the provided arguments."""
    start_time = time.time()

    credentials_root = os.path.join(AURORA_TOOLING_ROOT, "houdini_credentials")
    with credentials_root_context(credentials_root) as credentials_root:
        DEFAULT_MOUNT_PATHS[credentials_root] = "/mnt/credentials/"

        # Getting latest SideFX secrets and mounting them to the container
        aws_region = get_aws_region()
        all_sidefx_secrets = get_aws_secrets(aws_region, SIDEFX_SECRETS_NAME)
        json.dump(
            all_sidefx_secrets,
            open(
                os.path.join(credentials_root, "houdini_credentials.json"),
                "w",
                encoding="utf-8",
            ),
            indent=4,
        )

        mounted_work_directive_path = in_args.work_directive.replace(
            "$DATA_ROOT", DEFAULT_MOUNT_PATHS[DATA_ROOT]
        )

        # Run the automation script
        docker_utils.run_docker_compose_script_stream(
            service_name="houdini_aws:latest",
            script_path="/mnt/tooling/runtime/runner.sh",
            mount_paths=DEFAULT_MOUNT_PATHS,
            args=["--work_directive", mounted_work_directive_path],
            extra_docker_args=[],
            entrypoint="/bin/bash",
            environment={
                "AURORA_TOOLING_ROOT": "/mnt/tooling/",
                "DATA_ROOT": "/mnt/data/",
                "CREDENTIALS_ROOT": "/mnt/credentials/",
            },
        )

        # Update timings
        end_time = time.time()
        timings_dict["generate_houdini_content"] = end_time - start_time


if __name__ == "__main__":
    argparser = argparse.ArgumentParser("Houdini AWS Runner Script")
    argparser.add_argument(
        "--process_hip",
        help="Should Houdini process a work directive?",
        action="store_true",
    )
    argparser.add_argument(
        "--work_directive",
        type=str,
        default=os.path.join(
            AURORA_TOOLING_ROOT,
            "RUNTIME",
            "IN",
            "houdini_directive.json",
        ),
        help="The houdini_directive.json Houdini work directive to process.",
    )
    args = argparser.parse_args()

    timings = {}

    output_directory = os.path.join(DATA_ROOT, "OUT")
    os.makedirs(
        output_directory,
        exist_ok=True,
    )

    try:
        if args.process_hip:
            if args.work_directive is None:
                raise ValueError(
                    "The --work_directive argument must be provided when --process_hip is set."
                )
            generate_houdini_content(args, timings)

        with open(
            os.path.join(output_directory, "timings.json"), "w", encoding="utf-8"
        ) as f:
            json.dump(timings, f, indent=4)
    except Exception as e:
        raise e
