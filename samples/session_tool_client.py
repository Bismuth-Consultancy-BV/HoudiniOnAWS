"""
Aurora Session Client

This client connects to an Aurora Session via WebSocket, uploads a Houdini
Digital Asset, sends parameter updates, and receives geometry updates.

It speaks the same protocol as the web client in `webapp/`: the session is
started first, and the HDA is uploaded to S3 through a presigned URL and
installed afterwards.

Example usage:
    client = AuroraSessionClient(websocket_url="wss://xxx.execute-api.eu-north-1.amazonaws.com/production")
    await client.connect()
    await client.start_session()
    await client.wait_until_ready()
    await client.load_hda("MyTool.hda")
    await client.update_parameter("/obj/CONTAINER/user_hda/size", 5.0)
    geometry_url = client.get_last_geometry_url()
    await client.terminate()
"""

import json
import os
import asyncio
import websockets
import logging
import requests
from typing import Optional, Dict, Any, Callable
import argparse

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

#: Digital-asset extensions the session accepts, mirroring the web client.
HDA_EXTENSIONS = (".hda", ".hdalc", ".hdanc", ".otl", ".otllc", ".otlnc")


class AuroraSessionClient:
    """Client for Aurora Session (real-time Houdini via WebSocket)."""

    #: Seconds to wait for an EC2 instance to boot, license Houdini and connect.
    SESSION_READY_TIMEOUT = 420

    #: Seconds to wait for an HDA to install and report its parameters.
    PARAMETERS_TIMEOUT = 300

    def __init__(self, websocket_url: str):
        """
        Initialize the client.

        Args:
            websocket_url: WebSocket API Gateway URL (wss://...)
        """
        self.websocket_url = websocket_url
        self.websocket = None
        self.session_id = None
        self.instance_id = None
        self.last_geometry_url = None
        self.parameters = None
        self.ready = False
        self.message_handlers = []
        self.running = False
        # action name -> Future, resolved by receive_messages(). Lets a caller
        # await one specific reply while the receive loop drains the socket.
        self._waiters: Dict[str, asyncio.Future] = {}
        self._upload_seq = 0

    async def connect(self):
        """
        Connect to the WebSocket API and obtain a session id.

        No HDA is involved at this point - start the session first, then call
        :meth:`load_hda`.
        """
        logger.info(f"Connecting to {self.websocket_url}")

        self.websocket = await websockets.connect(self.websocket_url)
        self.running = True

        # Send initial message to get session_id
        await self.websocket.send(json.dumps({"action": "get_session_id"}))

        # Receive session_id
        response = await self.websocket.recv()
        data = json.loads(response)

        if "session_id" in data:
            self.session_id = data["session_id"]
            logger.info(f"Connected! Session ID: {self.session_id}")
        else:
            logger.error(f"Connection failed: {data}")
            raise Exception("Failed to establish session")

    async def send_command(self, action: str, **kwargs) -> Dict[str, Any]:
        """
        Send a command to the server.

        Args:
            action: Command action (start_session, update_parameter, etc.)
            **kwargs: Additional command parameters
        """
        if not self.websocket:
            raise Exception("Not connected. Call connect() first.")

        # Including the session id lets the routing Lambda look the session up
        # directly instead of falling back to a secondary-index query.
        command = {"action": action, "session_id": self.session_id, **kwargs}

        logger.info(f"Sending command: {action}")
        await self.websocket.send(json.dumps(command))

    async def receive_messages(self, callback: Optional[Callable] = None):
        """
        Continuously receive messages from server.

        Args:
            callback: Optional callback function for each message
        """
        try:
            while self.running and self.websocket:
                try:
                    message = await asyncio.wait_for(
                        self.websocket.recv(),
                        timeout=1.0
                    )
                    data = json.loads(message)

                    action = data.get("action")

                    # Heartbeats keep API Gateway awake during long cooks and
                    # carry nothing worth reporting.
                    if action == "heartbeat":
                        continue

                    logger.info(f"Received: {action or data.get('status')}")

                    # Update internal state
                    if "instance_id" in data:
                        self.instance_id = data["instance_id"]

                    if action == "session_identified" or data.get("status") == "ready":
                        self.ready = True
                        self._resolve("session_ready", data)

                    if action == "parameters_ready":
                        self.parameters = data.get("parameters")
                        self._resolve("parameters_ready", data)

                    if action == "upload_url_ready":
                        self._resolve(data.get("request_id") or "upload_url_ready", data)

                    geometry = data.get("geometry")
                    if isinstance(geometry, dict):
                        url = geometry.get("url") or geometry.get("geometry_url")
                        if url:
                            self.last_geometry_url = url
                            logger.info(f"New geometry available: {url}")

                    if data.get("error"):
                        logger.error(f"Server error: {data['error']}")
                        self._reject_all(data["error"])

                    # Call user callback
                    if callback:
                        callback(data)

                    # Call registered handlers
                    for handler in self.message_handlers:
                        handler(data)

                except asyncio.TimeoutError:
                    continue
                except websockets.exceptions.ConnectionClosed:
                    logger.info("WebSocket connection closed")
                    self.running = False
                    self._reject_all("WebSocket connection closed")
                    break

        except Exception as e:
            logger.error(f"Error in receive loop: {e}")
            self.running = False
            self._reject_all(str(e))

    async def start_session(self):
        """Start the EC2 instance and Houdini session."""
        await self.send_command("start_session")
        logger.info("Session start requested. Waiting for instance to be ready...")

    async def wait_until_ready(self, timeout: Optional[float] = None):
        """
        Wait until the Houdini session on the instance is ready to take commands.

        Requires :meth:`receive_messages` to be running as a background task.

        Args:
            timeout: Seconds to wait. Defaults to SESSION_READY_TIMEOUT - an
                instance boot includes licensing and a Houdini cold start.
        """
        if self.ready:
            return

        timeout = timeout or self.SESSION_READY_TIMEOUT
        logger.info(f"Waiting up to {timeout:.0f}s for the session to become ready...")
        await self._wait_for("session_ready", timeout)
        logger.info("Houdini session is ready")

    async def load_hda(self, hda_path: str, timeout: Optional[float] = None) -> Dict[str, Any]:
        """
        Upload a digital asset and install it in the running session.

        The session cannot reach a file on this machine, so the asset goes up
        to S3 through a presigned URL first, exactly as the web client does.

        Args:
            hda_path: Path to a local .hda (see HDA_EXTENSIONS) file.
            timeout: Seconds to wait for the asset's parameters. Defaults to
                PARAMETERS_TIMEOUT.

        Returns:
            The `parameters_ready` payload, whose "parameters" key holds the
            schema the web client builds its controls from.
        """
        if not os.path.isfile(hda_path):
            raise FileNotFoundError(f"HDA file not found: {hda_path}")

        filename = os.path.basename(hda_path)
        if not filename.lower().endswith(HDA_EXTENSIONS):
            raise ValueError(
                f"'{filename}' is not a Houdini digital asset "
                f"({', '.join(HDA_EXTENSIONS)})"
            )

        # 1. Ask the backend for a presigned PUT URL.
        self._upload_seq += 1
        request_id = f"up-{self._upload_seq}"
        await self.send_command(
            "request_upload_url",
            filename=filename,
            content_type="application/octet-stream",
            purpose="hda",
            request_id=request_id,
        )
        url_data = await self._wait_for(request_id, 30)

        # 2. Upload the file itself. requests is blocking, so keep it off the
        #    event loop or the receive task stalls for the whole upload.
        logger.info(f"Uploading {filename} to S3...")
        await asyncio.get_event_loop().run_in_executor(
            None, self._put_file, url_data["upload_url"], hda_path
        )
        logger.info(f"Uploaded {filename}")

        # 3. Tell the session to install it and report its parameters.
        await self.send_command(
            "extract_parameters",
            filename=filename,
            s3_key=url_data["s3_key"],
        )

        result = await self._wait_for(
            "parameters_ready", timeout or self.PARAMETERS_TIMEOUT
        )
        param_count = len(result.get("parameters", {}).get("parameters", {}))
        logger.info(f"HDA loaded: {param_count} parameters")
        return result

    async def update_parameter(self, param: str, value: Any, num_components: int = 1):
        """
        Update a Houdini parameter.

        Args:
            param: Parameter path (e.g., "/obj/CONTAINER/user_hda/size")
            value: New value
            num_components: 1 for scalars, 3 or 4 for vectors and colours,
                which are sent as a list of that many values.
        """
        await self.send_command(
            "update_parameter", param=param, value=value, num_components=num_components
        )
        logger.info(f"Parameter update sent: {param} = {value}")

    async def request_geometry(self, output_index: int = 0):
        """
        Ask the session to cook and export one of the asset's outputs.

        Args:
            output_index: Which HDA output to export. 0 is the output the web
                client shows in its viewport.
        """
        await self.send_command("get_geometry", output_index=output_index)

    async def terminate(self):
        """Terminate the session and close connection."""
        if self.websocket:
            await self.send_command("terminate_session")
            logger.info("Termination requested")
            await asyncio.sleep(2)
            await self.websocket.close()
            self.running = False
            logger.info("Connection closed")

    def add_message_handler(self, handler: Callable):
        """Add a callback for received messages."""
        self.message_handlers.append(handler)

    def get_last_geometry_url(self) -> Optional[str]:
        """Get the URL of the most recent geometry export."""
        return self.last_geometry_url

    # ------------------------------------------------------------------ #
    #  Internal helpers                                                    #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _put_file(upload_url: str, file_path: str) -> None:
        """Upload one file to a presigned S3 URL. Blocking - run in an executor."""
        with open(file_path, "rb") as f:
            response = requests.put(
                upload_url,
                data=f,
                headers={"Content-Type": "application/octet-stream"},
                timeout=300,
            )
        if not response.ok:
            raise RuntimeError(
                f"Upload failed: {response.status_code} {response.reason}"
            )

    async def _wait_for(self, key: str, timeout: float) -> Dict[str, Any]:
        """Wait for the message receive_messages() files under *key*."""
        future = asyncio.get_event_loop().create_future()
        self._waiters[key] = future
        try:
            return await asyncio.wait_for(future, timeout)
        except asyncio.TimeoutError:
            raise TimeoutError(f"Timed out after {timeout:.0f}s waiting for '{key}'")
        finally:
            self._waiters.pop(key, None)

    def _resolve(self, key: str, data: Dict[str, Any]) -> None:
        future = self._waiters.pop(key, None)
        if future and not future.done():
            future.set_result(data)

    def _reject_all(self, reason: str) -> None:
        """Fail every pending wait - used when the session errors or drops."""
        for key, future in list(self._waiters.items()):
            self._waiters.pop(key, None)
            if not future.done():
                future.set_exception(RuntimeError(reason))


# Example interactive usage
async def example_interactive_session():
    """Example of an interactive session."""

    # Get WebSocket URL from environment or config
    websocket_url = os.getenv(
        "HOUDINI_WEBSOCKET_URL",
        "wss://your-api-id.execute-api.eu-north-1.amazonaws.com/production"
    )
    hda_file = os.getenv("HOUDINI_HDA_FILE", "MyTool.hda")

    client = AuroraSessionClient(websocket_url)

    try:
        await client.connect()

        # Start background message receiver
        receive_task = asyncio.create_task(
            client.receive_messages(
                callback=lambda msg: print(f">> {msg}")
            )
        )

        # Start the session (launches EC2 instance)
        print("Starting Houdini session...")
        await client.start_session()

        # Booting the instance, licensing Houdini and loading the session HIP
        # takes a couple of minutes - wait for it rather than guessing.
        await client.wait_until_ready()

        # Upload and install the asset, then wait for its parameter schema
        print(f"\nLoading {hda_file}...")
        await client.load_hda(hda_file)

        # Parameter paths come from the schema; every asset is instantiated at
        # /obj/CONTAINER/user_hda, so they all share that prefix.
        for path in list(client.parameters.get("parameters", {}))[:3]:
            print(f"  parameter: {path}")

        # Update some parameters
        print("\nUpdating parameters...")
        await client.update_parameter("/obj/CONTAINER/user_hda/size", 1.0)
        await asyncio.sleep(1)

        await client.update_parameter("/obj/CONTAINER/user_hda/height", 2.0)
        await asyncio.sleep(1)

        # Get geometry URL
        if client.get_last_geometry_url():
            print(f"\nLatest geometry: {client.get_last_geometry_url()}")

        # Keep session alive for a bit
        print("\nSession running. Press Ctrl+C to terminate...")
        await asyncio.sleep(30)

    except KeyboardInterrupt:
        print("\nInterrupted by user")

    finally:
        # Clean up
        print("\nTerminating session...")
        await client.terminate()


# Command-line interface
async def cli_main():
    """Command-line interface for the client."""
    # Try to load websocket_url from tf_outputs.json
    default_websocket_url = None
    tf_outputs_path = os.path.join(os.path.dirname(__file__), "tf_outputs.json")
    if os.path.exists(tf_outputs_path):
        try:
            with open(tf_outputs_path, "r") as f:
                tf_outputs = json.load(f)
                default_websocket_url = tf_outputs.get("websocket_url")
                logger.info(f"Loaded websocket_url from tf_outputs.json: {default_websocket_url}")
        except Exception as e:
            logger.warning(f"Failed to load tf_outputs.json: {e}")

    parser = argparse.ArgumentParser(
        description="Interactive Houdini Tool Client"
    )
    parser.add_argument(
        "--websocket-url",
        default=default_websocket_url,
        required=default_websocket_url is None,
        help="WebSocket API Gateway URL (defaults to value from tf_outputs.json)"
    )
    parser.add_argument(
        "--hda-file",
        help="Path to a local Houdini digital asset to upload and load. "
             "Omit to start an empty session and load one later."
    )
    parser.add_argument(
        "--command",
        choices=["start", "interactive"],
        default="interactive",
        help="Command mode"
    )

    args = parser.parse_args()

    client = AuroraSessionClient(args.websocket_url)

    try:
        await client.connect()

        # Start message receiver
        receive_task = asyncio.create_task(
            client.receive_messages(
                callback=lambda msg: print(f"[Server] {json.dumps(msg, indent=2)}")
            )
        )

        # Start session
        print("Starting session...")
        await client.start_session()
        await client.wait_until_ready()

        if args.hda_file:
            print(f"Loading {args.hda_file}...")
            await client.load_hda(args.hda_file)

        if args.command == "interactive":
            # Interactive REPL
            print("\nInteractive mode. Commands:")
            print("  load <path>           - Upload and load an HDA")
            print("  param <path> <value>  - Update parameter")
            print("  params                - List loaded parameter paths")
            print("  cook                  - Re-export geometry")
            print("  status                - Show session state")
            print("  geometry              - Get latest geometry URL")
            print("  quit                  - Terminate session")
            print()

            while client.running:
                try:
                    cmd = await asyncio.get_event_loop().run_in_executor(
                        None, input, ">>> "
                    )

                    parts = cmd.strip().split()
                    if not parts:
                        continue

                    if parts[0] == "quit":
                        break
                    elif parts[0] == "load" and len(parts) >= 2:
                        await client.load_hda(parts[1])
                    elif parts[0] == "param" and len(parts) >= 3:
                        param_path = parts[1]
                        value = float(parts[2]) if "." in parts[2] else int(parts[2])
                        await client.update_parameter(param_path, value)
                    elif parts[0] == "params":
                        schema = (client.parameters or {}).get("parameters", {})
                        if not schema:
                            print("No HDA loaded")
                        for path, entry in schema.items():
                            print(f"  {path}  ({entry.get('type')})")
                    elif parts[0] == "cook":
                        await client.request_geometry()
                    elif parts[0] == "status":
                        print(f"  session id:   {client.session_id}")
                        print(f"  instance id:  {client.instance_id}")
                        print(f"  ready:        {client.ready}")
                        print(f"  hda loaded:   {bool(client.parameters)}")
                        print(f"  geometry url: {client.get_last_geometry_url()}")
                    elif parts[0] == "geometry":
                        url = client.get_last_geometry_url()
                        print(f"Geometry URL: {url if url else 'None'}")
                    else:
                        print("Unknown command")

                except EOFError:
                    break
                except Exception as e:
                    print(f"Error: {e}")

        else:
            # Just keep running
            await asyncio.sleep(60)

    finally:
        print("\nTerminating...")
        await client.terminate()


if __name__ == "__main__":
    # Run the example or CLI
    import sys

    if len(sys.argv) > 1:
        asyncio.run(cli_main())
    else:
        print("Running example interactive session...")
        print("Set HOUDINI_WEBSOCKET_URL environment variable or edit the script.")
        asyncio.run(example_interactive_session())
