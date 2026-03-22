"""
Aurora Session Houdini runner - processes commands using Houdini.
This runs in hython and imports hou module.
Uses asyncio ONLY for WebSocket client communication with local handler.
"""

import json
import logging
import os
import shutil
import sys
import time
import traceback
import asyncio
import concurrent.futures
import boto3
import tempfile
import websockets
from botocore.config import Config
from hda_utils import install_and_instantiate_hda, EXPORT_GLTF_PATH
from hda_utils import extract_hda_parameters
from hda_utils import export_gltf

# Setup logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Import Houdini
try:
    import hou
except ImportError:
    logger.error("Houdini (hou module) not found. Make sure this runs in hython.")
    sys.exit(1)


class HoudiniRunner:
    """Handles Houdini operations synchronously."""

    def __init__(
        self,
        session_id: str,
        s3_output_bucket: str,
        s3_client,
        session_hip: str = None,
        websocket=None,
        input_bucket: str = None,
    ):
        self.session_id = session_id
        self.session_hip = session_hip  # path to session_runner.hip
        self.s3_output_bucket = s3_output_bucket
        self.s3_client = s3_client
        self.input_bucket = input_bucket
        self.output_node = None
        self.hda_node = None  # set when user loads an HDA via menu
        self.last_geometry_url = None
        self.running = True
        self.websocket = websocket
        self.log_sink = None
        self._log_queue = []

        logger.info(f"Initializing Houdini runner for session {session_id}")
        logger.info(f"Session HIP: {session_hip}")

    def load_session(self) -> bool:
        """Load session_runner.hip template. HDA is loaded later via menu."""

        try:
            load_start = time.time()
            logger.info("=== SESSION LOADING START ===")

            session_hip_path = hou.text.expandString(self.session_hip)
            logger.info(f"Session HIP path: {session_hip_path}")

            if not os.path.exists(session_hip_path):
                logger.error(f"Session HIP not found: {session_hip_path}")
                return False

            logger.info("Loading session_runner.hip...")
            hou_load_start = time.time()
            hou.hipFile.load(session_hip_path)
            hou_load_time = time.time() - hou_load_start
            logger.info(f"session_runner.hip loaded in {hou_load_time:.2f}s")

            # The GLTF ROP is the output / export trigger
            self.output_node = hou.node(EXPORT_GLTF_PATH)
            if self.output_node:
                logger.info(f"Export ROP set to: {self.output_node.path()}")
            else:
                logger.warning(f"GLTF ROP not found at {EXPORT_GLTF_PATH}")

            # Setup Houdini log capturing
            logger.info("Setting up Houdini log capturing...")
            self.setup_log_capturing()
            logger.info("Log capturing enabled")

            load_time = time.time() - load_start
            logger.info(f"=== SESSION LOADING COMPLETE ({load_time:.2f}s) ===")

            return True

        except Exception as e:
            logger.error(f"Error loading session/HDA: {e}")
            traceback.print_exc()
            return False

    def setup_log_capturing(self):
        """Setup Houdini log capturing to send logs to client."""
        try:

            def log_callback(log_entry) -> bool:
                """Callback to capture Houdini logs and send to client."""
                severity = log_entry.severity()
                message = log_entry.message()
                context = log_entry.sourceContext()

                # Determine log level
                if severity == hou.severityType.Fatal:
                    level = "fatal"
                    logger.error(f"[Houdini FATAL] {context}: {message}")
                elif severity == hou.severityType.Error:
                    level = "error"
                    logger.error(f"[Houdini ERROR] {context}: {message}")
                elif severity == hou.severityType.Warning:
                    level = "warning"
                    logger.warning(f"[Houdini WARNING] {context}: {message}")
                elif severity == hou.severityType.Message:
                    level = "info"
                    logger.info(f"[Houdini INFO] {context}: {message}")
                else:
                    level = "info"

                # Send to client via websocket (will be queued if websocket not set yet)
                self.send_log_to_client(level, message, context)

                # Return True to suppress the log from normal Houdini output
                return severity in [hou.severityType.Error, hou.severityType.Fatal]

            # Create a temporary log file
            log_dir = tempfile.gettempdir()
            log_path = os.path.join(log_dir, f"houdini_{self.session_id}.log")

            # Setup file sink
            self.log_sink = hou.logging.FileSink(log_path)
            self.log_sink.connect("Node Errors")
            self.log_sink.connect("Standard Error")
            self.log_sink.connect("Standard Output")
            self.log_sink.setFilterCallback(log_callback)

            logger.info(f"Houdini log capturing enabled, logging to: {log_path}")
            self.send_log_to_client("system", "Houdini log capturing enabled", "System")

        except Exception as e:
            logger.error(f"Failed to setup Houdini log capturing: {e}")

    def send_log_to_client(self, level: str, message: str, context: str = ""):
        """Send a log message to the client via WebSocket."""
        self._log_queue.append(
            {
                "action": "log",
                "level": level,
                "message": message,
                "context": context,
                "timestamp": time.time(),
            }
        )

    def get_pending_logs(self):
        """Get and clear pending logs for sending."""
        logs = self._log_queue.copy()
        self._log_queue.clear()
        return logs

    def process_command(self, command: dict) -> dict:
        """Process a command synchronously and return result."""
        action = command.get("action")

        logger.info(f"Processing command: {action}")

        try:
            if action == "extract_parameters":
                return self.extract_parameters(command)
            elif action == "update_parameter":
                return self.update_parameter(command)
            elif action == "get_geometry":
                geometry_data = self.export_geometry()
                return {"action": "geometry_ready", "geometry": geometry_data}
            elif action == "execute_python":
                return self.execute_python(command)
            elif action == "terminate":
                self.running = False
                return {"status": "terminating"}
            else:
                logger.warning(f"Unknown action: {action}")
                return {"status": "received", "action": action}

        except Exception as e:
            logger.error(f"Error processing command: {e}")
            return {"error": str(e)}

    def extract_parameters(self, command: dict) -> dict:
        """Download HDA from S3, install it, and extract parameters.

        Always expects 's3_key' and 'filename' in the command.
        Supports loading the first HDA or swapping to a different one.
        """
        try:
            s3_key = command.get("s3_key")
            filename = command.get("filename")

            if not s3_key or not filename:
                return {"error": "Missing s3_key or filename. Use Session > Load HDA."}

            logger.info(f"Loading HDA: {filename} (s3: {s3_key})")

            # Download HDA from S3
            local_hda_path = os.path.join(
                os.environ.get("DATA_ROOT", "/tmp"), "user_tool.hda"
            )

            input_bucket = self.input_bucket or os.environ.get("INPUT_BUCKET")
            if not input_bucket:
                return {"error": "INPUT_BUCKET not configured — cannot download HDA."}

            logger.info(f"Downloading HDA from s3://{input_bucket}/{s3_key}")
            download_start = time.time()
            self.s3_client.download_file(input_bucket, s3_key, local_hda_path)
            download_time = time.time() - download_start
            logger.info(f"HDA downloaded in {download_time:.2f}s")

            # Install and instantiate the HDA (replaces previous one if any)
            hda_start = time.time()
            self.hda_node = install_and_instantiate_hda(local_hda_path)
            hda_time = time.time() - hda_start
            logger.info(f"HDA installed in {hda_time:.2f}s: {self.hda_node.path()}")

            param_data = extract_hda_parameters(self.hda_node)

            node_count = len(hou.node("/").allSubChildren())
            param_count = len(param_data["parameters"])

            msg = (
                f"Extracted {param_count} parameters from "
                f"'{param_data.get('tool_name')}' — "
                f"session has {node_count} nodes"
            )
            logger.info(msg)

            return {
                "action": "parameters_ready",
                "parameters": param_data,
                "node_count": node_count,
                "message": msg,
            }

        except Exception as e:
            logger.error(f"Error extracting HDA parameters: {e}")
            traceback.print_exc()
            self.hda_node = None
            return {"error": f"Failed to extract parameters: {str(e)}"}

    def update_parameter(self, command: dict) -> dict:
        """Update a Houdini parameter and export resulting geometry."""
        param_path = command.get("param")
        value = command.get("value")
        num_components = command.get("num_components", 1)

        if not param_path or value is None:
            return {"error": "Missing param or value"}

        try:
            update_start = time.time()
            logger.info(f"--- Parameter Update Start: {param_path} ---")

            # Multi-component parameters (vectors, colors) arrive as lists
            if isinstance(value, list) and num_components > 1:
                parm_tuple = hou.parmTuple(param_path)
                if not parm_tuple:
                    # Try individual component names
                    parm = hou.parm(param_path)
                    if not parm:
                        logger.error(f"Parameter not found: {param_path}")
                        return {"error": f"Parameter not found: {param_path}"}
                    old_value = parm.eval()
                    parm.set(value[0])
                else:
                    old_value = [p.eval() for p in parm_tuple]
                    parm_tuple.set(value)
            else:
                parm = hou.parm(param_path)
                if not parm:
                    logger.error(f"Parameter not found: {param_path}")
                    return {"error": f"Parameter not found: {param_path}"}
                old_value = parm.eval()
                parm.set(value)

            logger.info(f"Parameter updated: {param_path} = {value} (was {old_value})")

            # No explicit cook needed here — the GLTF ROP render in
            # export_geometry() will cook the full dependency chain
            # (EXPORT_NODE_REF -> HDA) automatically.

            # Export geometry via GLTF ROP
            logger.info("Exporting geometry...")
            geometry_result = self.export_geometry()

            update_time = time.time() - update_start
            logger.info(f"--- Parameter Update Complete ({update_time:.3f}s total) ---")

            # Check if geometry export failed
            if "error" in geometry_result:
                logger.error(f"Geometry export failed: {geometry_result['error']}")
                return {
                    "action": "geometry_ready",
                    "status": "error",
                    "error": f"Geometry export failed: {geometry_result['error']}",
                    "param": param_path,
                    "geometry": geometry_result,
                }

            return {
                "action": "geometry_ready",
                "status": "success",
                "param": param_path,
                "old_value": old_value,
                "new_value": value,
                "geometry": geometry_result,
            }

        except Exception as e:
            logger.error(f"Error updating parameter: {e}")
            return {"error": str(e)}

    def export_geometry(self) -> dict:
        """Export geometry via GLTF ROP and upload to S3."""

        try:
            export_start = time.time()

            # Create a temp directory for the export
            export_dir = tempfile.mkdtemp(prefix="houdini_export_")

            # Trigger the GLTF ROP render
            logger.info("Triggering GLTF export ROP...")
            render_start = time.time()
            gltf_path = export_gltf(output_dir=export_dir)
            render_time = time.time() - render_start
            logger.info(f"GLTF ROP rendered in {render_time:.3f}s")

            if not gltf_path or not os.path.exists(gltf_path):
                # Fallback: look for any .gltf or .glb in the export dir
                for fname in os.listdir(export_dir):
                    if fname.endswith((".gltf", ".glb")):
                        gltf_path = os.path.join(export_dir, fname)
                        break

            if not gltf_path or not os.path.exists(gltf_path):
                logger.error("GLTF export produced no output file")
                return {"error": "GLTF export produced no output file"}

            file_size = os.path.getsize(gltf_path)
            ext = os.path.splitext(gltf_path)[1]  # .gltf or .glb
            logger.info(
                f"Exported file: {gltf_path} ({file_size} bytes, {file_size/1024:.2f} KB)"
            )

            # Upload to S3
            s3_key = f"interactive/{self.session_id}/geometry_{int(time.time())}{ext}"
            logger.info(f"Uploading to S3: s3://{self.s3_output_bucket}/{s3_key}")
            upload_start = time.time()
            self.s3_client.upload_file(gltf_path, self.s3_output_bucket, s3_key)
            upload_time = time.time() - upload_start
            logger.info(f"S3 upload completed in {upload_time:.3f}s")

            # Also upload any sidecar files (.bin, textures) that may be alongside the gltf
            gltf_dir = os.path.dirname(gltf_path)
            gltf_basename = os.path.basename(gltf_path)
            for sidecar in os.listdir(gltf_dir):
                if sidecar == gltf_basename:
                    continue
                sidecar_path = os.path.join(gltf_dir, sidecar)
                if os.path.isfile(sidecar_path):
                    sidecar_key = f"interactive/{self.session_id}/{sidecar}"
                    self.s3_client.upload_file(
                        sidecar_path, self.s3_output_bucket, sidecar_key
                    )
                    logger.info(f"Uploaded sidecar: {sidecar}")

            # Generate presigned URL (valid for 1 hour)
            logger.info("Generating presigned URL...")
            geometry_url = self.s3_client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self.s3_output_bucket, "Key": s3_key},
                ExpiresIn=3600,
            )

            self.last_geometry_url = geometry_url

            # Try to get point/prim counts from the HDA output
            point_count = 0
            prim_count = 0
            if self.hda_node:
                try:
                    geo = self.hda_node.geometry()
                    if geo:
                        point_count = geo.intrinsicValue("pointcount")
                        prim_count = geo.intrinsicValue("primitivecount")
                except Exception:
                    pass

            # Clean up temp directory
            shutil.rmtree(export_dir, ignore_errors=True)

            export_time = time.time() - export_start
            logger.info(
                f"Geometry export complete in {export_time:.3f}s "
                f"(render: {render_time:.3f}s, upload: {upload_time:.3f}s)"
            )

            return {
                "status": "success",
                "url": geometry_url,
                "geometry_url": geometry_url,
                "s3_key": s3_key,
                "format": "gltf",
                "point_count": point_count,
                "primitive_count": prim_count,
            }

        except Exception as e:
            logger.error(f"Error exporting geometry: {e}")
            traceback.print_exc()
            return {"error": str(e)}

    def execute_python(self, command: dict) -> dict:
        """Execute arbitrary Python code in Houdini context."""
        code = command.get("code")
        if not code:
            return {"error": "No code provided"}

        try:
            # Execute in local context with hou available
            local_vars = {"hou": hou, "runner": self}
            exec(code, {"__builtins__": __builtins__}, local_vars)

            # Get result if any
            result = local_vars.get("result", "Code executed successfully")

            return {"status": "success", "result": str(result)}

        except Exception as e:
            logger.error(f"Error executing Python code: {e}")
            return {"error": str(e)}


# ======================================================================
#  Startup helpers — config loading, S3 init, ready-signal polling
# ======================================================================

# Mapping from OS environment variable name → JSON config key.
# The entrypoint writes config as JSON because env vars set after
# forking hython aren't visible to this process.
_ENV_MAPPING = {
    "SESSION_ID": "session_id",
    "SESSION_HIP": "session_hip",
    "WEBSOCKET_URL": "websocket_url",
    "AWS_REGION": "aws_region",
    "INPUT_BUCKET": "input_bucket",
    "S3_OUTPUT_BUCKET": "s3_output_bucket",
    "IDLE_TIMEOUT_SECONDS": "idle_timeout_seconds",
    "IDLE_WARNING_SECONDS": "idle_warning_seconds",
    "API_ENDPOINT": "api_endpoint",
    "LOCAL_WS_PORT": "local_ws_port",
    "DATA_ROOT": "data_root",
    "AURORA_TOOLING_ROOT": "aurora_tooling_root",
}


async def _wait_for_ready_signal(signal_path: str, timeout_seconds: int = 300) -> dict:
    """
    Poll for the ready-signal file written by entrypoint.sh.

    The entrypoint launches hython early to overlap its cold-start with
    licensing and S3 downloads. Once those are done it writes a JSON
    config file to *signal_path*.

    Args:
        signal_path:     Path to poll for.
        timeout_seconds: Max seconds to wait before raising.

    Returns:
        The parsed JSON config dict.

    Raises:
        TimeoutError: If the signal file is not created in time.
    """
    logger.info(f"Waiting for environment ready signal: {signal_path}")
    wait_start = time.time()

    while not os.path.exists(signal_path):
        elapsed = time.time() - wait_start
        if elapsed > timeout_seconds:
            raise TimeoutError(
                f"Timed out waiting for ready signal after {timeout_seconds}s"
            )
        if int(elapsed) % 10 == 0 and int(elapsed) > 0:
            logger.info(f"  Still waiting for ready signal... ({elapsed:.0f}s)")
        await asyncio.sleep(0.5)

    wait_time = time.time() - wait_start
    logger.info(f"Ready signal received after {wait_time:.2f}s wait")

    logger.info("Reading configuration from ready signal file...")
    with open(signal_path) as f:
        return json.load(f)


def _apply_config_to_env(config: dict) -> None:
    """Inject config values into ``os.environ`` for downstream code."""
    for env_key, json_key in _ENV_MAPPING.items():
        if json_key in config and config[json_key]:
            os.environ[env_key] = str(config[json_key])


def _create_s3_client(aws_region: str):
    """
    Create a boto3 S3 client with SigV4 and a regional endpoint.

    The regional endpoint is required so that presigned URLs resolve
    to the correct host.
    """
    client = boto3.client(
        "s3",
        region_name=aws_region,
        endpoint_url=f"https://s3.{aws_region}.amazonaws.com",
        config=Config(signature_version="s3v4"),
    )
    logger.info(
        f"S3 client initialized for region {aws_region} "
        f"with SigV4 (regional endpoint)"
    )
    return client


def _create_runner(config: dict, s3_client) -> "HoudiniRunner":
    """
    Instantiate a :class:`HoudiniRunner` from a config dict and load
    the session HIP file.

    Returns:
        A ready-to-use ``HoudiniRunner``.

    Raises:
        SystemExit: If the HIP file fails to load.
    """
    runner = HoudiniRunner(
        session_id=config["session_id"],
        session_hip=config.get("session_hip"),
        s3_output_bucket=config["s3_output_bucket"],
        s3_client=s3_client,
        input_bucket=config.get("input_bucket"),
    )

    logger.info("Loading session HIP...")
    hip_load_start = time.time()
    if not runner.load_session():
        logger.error("Failed to load session. Exiting.")
        sys.exit(1)
    logger.info(f"Session loaded in {time.time() - hip_load_start:.2f}s")

    return runner


# ======================================================================
#  RunnerClient — async WebSocket bridge between handler & HoudiniRunner
# ======================================================================


class RunnerClient:
    """
    Async client that connects the local :class:`HoudiniRunner`
    to the :class:`WebSocketBridge` via a local WebSocket.

    Responsibilities:
    - Connect with retries to the local WebSocket server.
    - Dispatch incoming commands to the runner on a thread-pool executor.
    - Send back responses and forward pending Houdini logs.
    - Emit periodic heartbeats to keep API Gateway alive during long cooks.
    """

    MAX_RETRIES = 10
    RETRY_DELAY = 2  # seconds
    RECV_TIMEOUT = 0.5  # seconds
    HEARTBEAT_INTERVAL = 60  # seconds

    def __init__(self, runner: HoudiniRunner, ws_url: str):
        self._runner = runner
        self._ws_url = ws_url

    async def run(self, start_time: float = None) -> None:
        """Connect to the handler and enter the message loop."""
        start_time = start_time or time.time()

        logger.info("=" * 60)
        logger.info(f"Connecting to WebSocket handler at {self._ws_url}")

        for attempt in range(self.MAX_RETRIES):
            try:
                logger.info(f"Connection attempt {attempt + 1}/{self.MAX_RETRIES}...")
                connect_start = time.time()

                async with websockets.connect(
                    self._ws_url, ping_interval=30, ping_timeout=10
                ) as ws:
                    connect_time = time.time() - connect_start
                    logger.info(
                        f"Connected to local WebSocket handler "
                        f"in {connect_time:.2f}s"
                    )

                    total = time.time() - start_time
                    logger.info("=" * 60)
                    logger.info(f"=== HOUDINI RUNNER READY (total: {total:.2f}s) ===")
                    logger.info("=" * 60)

                    self._runner.websocket = ws
                    await self._message_loop(ws)
                    return  # clean exit

            except ConnectionRefusedError:
                if attempt < self.MAX_RETRIES - 1:
                    logger.warning(
                        f"Connection refused, retrying in {self.RETRY_DELAY}s..."
                    )
                    await asyncio.sleep(self.RETRY_DELAY)
                else:
                    logger.error(
                        "Failed to connect to local WebSocket handler "
                        "after all retries"
                    )
                    sys.exit(1)

            except Exception as e:
                logger.error(f"Error in WebSocket connection: {e}")
                if attempt < self.MAX_RETRIES - 1:
                    await asyncio.sleep(self.RETRY_DELAY)
                else:
                    sys.exit(1)

    # ------------------------------------------------------------------ #

    async def _message_loop(self, ws) -> None:
        """Core command receive → execute → respond loop."""
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        keepalive = asyncio.create_task(self._keepalive(ws))

        try:
            while self._runner.running:
                try:
                    message = await asyncio.wait_for(
                        ws.recv(), timeout=self.RECV_TIMEOUT
                    )

                    command = json.loads(message)
                    action = command.get("action")
                    logger.info(f"Received command: {action}")

                    # Run blocking work in a thread to keep WS pings alive
                    loop = asyncio.get_event_loop()
                    result = await loop.run_in_executor(
                        executor, self._runner.process_command, command
                    )

                    await ws.send(json.dumps(result))
                    logger.info(f"Sent response for {action}")

                    # Auto-export initial geometry after parameter extraction
                    if (
                        action == "extract_parameters"
                        and "error" not in result
                        and self._runner.hda_node
                    ):
                        logger.info("Exporting initial geometry...")
                        geo_result = await loop.run_in_executor(
                            executor, self._runner.export_geometry
                        )
                        await ws.send(
                            json.dumps(
                                {
                                    "action": "geometry_ready",
                                    "geometry": geo_result,
                                }
                            )
                        )
                        logger.info("Sent initial geometry_ready")

                    await self._flush_logs(ws)

                except asyncio.TimeoutError:
                    await self._flush_logs(ws)

                except websockets.exceptions.ConnectionClosed:
                    logger.info("WebSocket connection closed")
                    self._runner.running = False
                    break

        finally:
            keepalive.cancel()
            executor.shutdown(wait=False)

    async def _keepalive(self, ws) -> None:
        """Periodic heartbeat to prevent API Gateway idle disconnect."""
        while self._runner.running:
            await asyncio.sleep(self.HEARTBEAT_INTERVAL)
            try:
                await ws.send(
                    json.dumps(
                        {
                            "action": "heartbeat",
                            "timestamp": time.time(),
                        }
                    )
                )
            except Exception:
                break

    async def _flush_logs(self, ws) -> None:
        """Forward any queued Houdini log messages to the handler."""
        for log_msg in self._runner.get_pending_logs():
            try:
                await ws.send(json.dumps(log_msg))
            except Exception as e:
                logger.error(f"Error sending log: {e}")


# ======================================================================
#  Entry point
# ======================================================================


async def main():
    """Main entry point — connects to local WebSocket handler."""
    start_time = time.time()
    logger.info("=" * 60)
    logger.info("=== HOUDINI RUNNER STARTING ===")
    logger.info("=" * 60)

    # Wait for the entrypoint to finish licensing / env setup
    ready_signal = os.getenv("HYTHON_READY_SIGNAL", "/tmp/houdini_boot_ready")
    config = await _wait_for_ready_signal(ready_signal)
    _apply_config_to_env(config)
    logger.info(f"Config loaded: {json.dumps(config, indent=2)}")

    # Validate required fields
    if not config.get("s3_output_bucket"):
        logger.error("S3_OUTPUT_BUCKET not set. Cannot start.")
        sys.exit(1)

    local_ws_port = int(config.get("local_ws_port", 7007))
    logger.info(f"Configuration:")
    logger.info(f"  Session ID: {config.get('session_id')}")
    logger.info(f"  Session HIP: {config.get('session_hip')}")
    logger.info(f"  S3 bucket: {config.get('s3_output_bucket')}")
    logger.info(f"  Input bucket: {config.get('input_bucket')}")
    logger.info(f"  Local WebSocket port: {local_ws_port}")

    # Init S3 and Houdini runner
    s3_client = _create_s3_client(config.get("aws_region"))
    runner = _create_runner(config, s3_client)

    # Connect and run
    client = RunnerClient(runner, f"ws://127.0.0.1:{local_ws_port}")

    try:
        await client.run(start_time=start_time)
    except KeyboardInterrupt:
        logger.info("Received interrupt signal")

    logger.info("Houdini runner ended")


if __name__ == "__main__":
    asyncio.run(main())
