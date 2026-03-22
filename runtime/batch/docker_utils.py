import logging
import os
import platform
import subprocess
import time
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# ── Linux GPU / display mounts required for headless Houdini (Vulkan) ──
_LINUX_VOLUME_MOUNTS: List[str] = [
    "/tmp/.X11-unix:/tmp/.X11-unix",
    "/run/user/1000:/run/user/1000",
    "/usr/share/vulkan/icd.d:/usr/share/vulkan/icd.d:ro",
    "/usr/lib/x86_64-linux-gnu/libGLX_nvidia.so.0:/usr/lib/x86_64-linux-gnu/libGLX_nvidia.so.0:ro",
    "/usr/lib/x86_64-linux-gnu/libvulkan.so.1:/usr/lib/x86_64-linux-gnu/libvulkan.so.1:ro",
]

# ── Container environment variables for NVIDIA / Houdini ──
_CONTAINER_ENV: Dict[str, str] = {
    "NVIDIA_DRIVER_CAPABILITIES": "all",
    "NVIDIA_VISIBLE_DEVICES": "all",
    "HOUDINI_OCL_DEVICETYPE": "CPU",
    "SDL_VIDEODRIVER": "dummy",
    "DISPLAY": ":99",
    "HOUDINI_VULKAN_VIEWER": "0",
    "XDG_RUNTIME_DIR": "/run/user/1000",
}


def cleanup_docker_container(service_name: str) -> None:
    """
    Ensures any running container for the given service is stopped and removed.
    """
    logger.info("Ensuring %s container is terminated...", service_name)

    try:
        # Get running container ID for the service
        container_id = (
            subprocess.check_output(
                ["docker", "ps", "-q", "--filter", f"name={service_name}"]
            )
            .decode()
            .strip()
        )

        if container_id:
            logger.info("Stopping and removing container: %s", container_id)
            try:
                subprocess.run(["docker", "stop", container_id], check=True)
            except subprocess.CalledProcessError as e:
                logger.warning("Failed to stop container %s: %s", container_id, e)

            try:
                subprocess.run(["docker", "rm", "-f", container_id], check=True)
            except subprocess.CalledProcessError as e:
                logger.warning("Failed to remove container %s: %s", container_id, e)
        else:
            logger.info("No running container found for service: %s", service_name)

    except subprocess.CalledProcessError as e:
        logger.error("Error checking running containers: %s", e)


def run_docker_compose_script_stream(
    service_name: str,
    script_path: str,
    entrypoint: str = "python3",
    mount_paths: Dict[str, str] = None,
    extra_docker_args: Optional[List[str]] = None,
    args: Optional[List[str]] = None,
    environment: Optional[dict] = None,
    timeout: int = 20000,
) -> None:
    """
    Runs a script inside a Docker container with specified mount paths and environment variables."
    """
    cmd = [
        "docker",
        "run",
        "--rm",
        "--gpus",
        "all",
        "--ipc",
        "host",
        "--runtime=nvidia",
        "--entrypoint",
        entrypoint,
    ]

    if mount_paths is None:
        mount_paths = {}

    # Mount the user specified directories into the container
    if len(mount_paths) > 0:
        for mount_path, mount_target in mount_paths.items():
            if not os.path.exists(mount_path):
                raise ValueError(f"Mount path does not exist: {mount_path}")
            cmd.extend(["-v", f"{mount_path}:{mount_target}:rw"])

    # Mount vulkan specific files. These are required for Houdini to work correctly.
    if platform.system() == "Linux":
        for vol in _LINUX_VOLUME_MOUNTS:
            cmd.extend(["-v", vol])

    if extra_docker_args:
        cmd.extend(extra_docker_args)

    # Inject GPU / Houdini environment variables
    for key, value in {**_CONTAINER_ENV, **(environment or {})}.items():
        cmd.extend(["-e", f"{key}={value}"])

    cmd.append(service_name)
    cmd.append(script_path)

    if args:
        cmd.extend(args)

    try:
        start_time = time.time()

        process = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1
        )
        log_dir = os.path.expandvars("$AURORA_TOOLING_ROOT/logs")
        os.makedirs(log_dir, exist_ok=True)
        log_path = os.path.join(log_dir, "runtime.log")
        with open(log_path, "w", encoding="utf-8") as log_fh:
            command = " ".join(cmd)
            logger.info("Running Docker command: %s", command)
            log_fh.write(f"Running Docker command: {command}\n")
            log_fh.flush()
            while True:
                output = process.stdout.readline()
                if process.poll() is not None:
                    break

                if output:
                    logger.info(output.strip())
                    log_fh.write(output)
                    log_fh.flush()

                # Enforce timeout
                if time.time() - start_time > timeout:
                    logger.warning(
                        "Timeout reached! Killing Docker container for %s.",
                        service_name,
                    )
                    process.terminate()
                    break
    except Exception as e:
        logger.error("Error running command: %s", e)
        raise
    finally:
        # Ensures the returncode gets set
        process.wait()

        # Force cleanup of the container
        cleanup_docker_container(service_name)

        # Check for errors after process completion
        if process.returncode != 0:
            raise RuntimeError(
                f"Process failed with non-zero exit code ({process.returncode}). Check logs for details."
            )
