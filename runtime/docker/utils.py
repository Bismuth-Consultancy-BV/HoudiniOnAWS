import os
import subprocess
import time
import platform
from typing import List, Optional, Dict


def cleanup_docker_container(service_name: str) -> None:
    """
    Ensures any running container for the given service is stopped and removed.
    """
    print(f"Ensuring {service_name} container is terminated...")

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
            print(f"Stopping and removing container: {container_id}")
            try:
                subprocess.run(["docker", "stop", container_id], check=True)
            except subprocess.CalledProcessError as e:
                print(f"Warning: Failed to stop container {container_id}: {e}")

            try:
                subprocess.run(["docker", "rm", "-f", container_id], check=True)
            except subprocess.CalledProcessError as e:
                print(f"Warning: Failed to remove container {container_id}: {e}")
        else:
            print(f"No running container found for service: {service_name}")

    except subprocess.CalledProcessError as e:
        print(f"Error checking running containers: {e}")


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

    # Mount vulkan specific files. These are required for Unreal to work correctly.
    if platform.system() == "Linux":
        cmd.extend(["-v", "/tmp/.X11-unix:/tmp/.X11-unix"])
        cmd.extend(["-v", "/run/user/1000:/run/user/1000"])
        cmd.extend(["-v", "/usr/share/vulkan/icd.d:/usr/share/vulkan/icd.d:ro"])
        cmd.extend(
            [
                "-v",
                "/usr/lib/x86_64-linux-gnu/libGLX_nvidia.so.0:/usr/lib/x86_64-linux-gnu/libGLX_nvidia.so.0:ro",
            ]
        )
        cmd.extend(
            [
                "-v",
                "/usr/lib/x86_64-linux-gnu/libvulkan.so.1:/usr/lib/x86_64-linux-gnu/libvulkan.so.1:ro",
            ]
        )

    if extra_docker_args:
        cmd.extend(extra_docker_args)

    if environment:
        for key, value in environment.items():
            cmd.extend(["-e", f"{key}={value}"])

    # Passing in NVIDIA env vars
    cmd.extend(["-e", "NVIDIA_DRIVER_CAPABILITIES=all"])
    cmd.extend(["-e", "NVIDIA_VISIBLE_DEVICES=all"])
    cmd.extend(["-e", "HOUDINI_OCL_DEVICETYPE=CPU"])
    cmd.extend(["-e", "SDL_VIDEODRIVER=dummy"])
    cmd.extend(["-e", "DISPLAY=:99"])
    cmd.extend(["-e", "HOUDINI_VULKAN_VIEWER=0"])
    cmd.extend(["-e", "XDG_RUNTIME_DIR=/run/user/1000"])

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
        log_file = os.path.join(log_dir, "runtime.log")
        with open(log_file, "w", encoding="utf-8") as log_file:
            command = " ".join(cmd)
            print(f"Running Docker command: {command}")
            log_file.write(f"Running Docker command: {command}")
            log_file.flush()
            while True:
                output = process.stdout.readline()
                if process.poll() is not None:
                    break

                if output:
                    print(output.strip())
                    log_file.write(output)
                    log_file.flush()

                # Enforce timeout
                if time.time() - start_time > timeout:
                    print(
                        f"Timeout reached! Killing Docker container for {service_name}."
                    )
                    process.terminate()
                    break
    except Exception as e:
        print(f"Error running command: {e}")
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
