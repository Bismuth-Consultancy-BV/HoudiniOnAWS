"""
Aurora Session Client

This client connects to an Aurora Session via WebSocket,
sends parameter updates, and receives geometry updates.

Example usage:
    client = AuroraSessionClient(websocket_url="wss://xxx.execute-api.us-east-1.amazonaws.com/production")
    client.connect(hda_file="MyTool.hda")
    client.start_session()
    client.update_parameter("/obj/CONTAINER/user_hda/size", 5.0)
    geometry_url = client.get_last_geometry_url()
    client.terminate()
"""

import json
import os
import asyncio
import websockets
import logging
from typing import Optional, Dict, Any, Callable
import argparse

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class AuroraSessionClient:
    """Client for Aurora Session (real-time Houdini via WebSocket)."""
    
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
        self.message_handlers = []
        self.running = False
        
    async def connect(self, hda_file: str = "tool.hda"):
        """
        Connect to WebSocket and initialize session.
        
        Args:
            hda_file: Name of the Houdini Digital Asset file to load
        """
        url_with_params = f"{self.websocket_url}?hda_file={hda_file}"
        
        logger.info(f"Connecting to {url_with_params}")
        
        self.websocket = await websockets.connect(url_with_params)
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
        
        command = {"action": action, **kwargs}
        
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
                    
                    logger.info(f"Received: {data.get('action')}")
                    
                    # Update internal state
                    if "instance_id" in data:
                        self.instance_id = data["instance_id"]
                    
                    if "geometry" in data and "geometry_url" in data["geometry"]:
                        self.last_geometry_url = data["geometry"]["geometry_url"]
                        logger.info(f"New geometry available: {self.last_geometry_url}")
                    
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
                    break
        
        except Exception as e:
            logger.error(f"Error in receive loop: {e}")
            self.running = False
    
    async def start_session(self):
        """Start the EC2 instance and Houdini session."""
        await self.send_command("start_session")
        logger.info("Session start requested. Waiting for instance to be ready...")
        
        # The response will come through receive_messages loop
        # Just wait for the ready status to be set
        # (in a real implementation, you'd use an asyncio.Event to signal readiness)
    
    async def update_parameter(self, param: str, value: Any):
        """
        Update a Houdini parameter.
        
        Args:
            param: Parameter path (e.g., "/obj/geo1/transform1/tx")
            value: New value
        """
        await self.send_command("update_parameter", param=param, value=value)
        logger.info(f"Parameter update sent: {param} = {value}")
    
    async def get_status(self):
        """Request current session status."""
        await self.send_command("get_status")
    
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


# Example interactive usage
async def example_interactive_session():
    """Example of an interactive session."""
    
    # Get WebSocket URL from environment or config
    websocket_url = os.getenv(
        "HOUDINI_WEBSOCKET_URL",
        "wss://your-api-id.execute-api.us-east-1.amazonaws.com/production"
    )
    
    client = AuroraSessionClient(websocket_url)
    
    try:
        # Connect and specify which HDA to load
        await client.connect(hda_file="MyTool.hda")
        
        # Start background message receiver
        receive_task = asyncio.create_task(
            client.receive_messages(
                callback=lambda msg: print(f">> {msg}")
            )
        )
        
        # Start the session (launches EC2 instance)
        print("Starting Houdini session...")
        await client.start_session()
        
        # Wait a moment for things to settle
        await asyncio.sleep(2)
        
        # Update some parameters
        print("\nUpdating parameters...")
        await client.update_parameter("/obj/CONTAINER/user_hda/size", 1.0)
        await asyncio.sleep(1)
        
        await client.update_parameter("/obj/CONTAINER/user_hda/height", 2.0)
        await asyncio.sleep(1)
        
        await client.update_parameter("/obj/CONTAINER/user_hda/divisions", 8)
        await asyncio.sleep(1)
        
        # Get status
        print("\nChecking status...")
        await client.get_status()
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
        default="tool.hda",
        help="Houdini Digital Asset (.hda) file to load"
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
        await client.connect(hda_file=args.hda_file)
        
        # Start message receiver
        receive_task = asyncio.create_task(
            client.receive_messages(
                callback=lambda msg: print(f"[Server] {json.dumps(msg, indent=2)}")
            )
        )
        
        # Start session
        print("Starting session...")
        await client.start_session()
        
        if args.command == "interactive":
            # Interactive REPL
            print("\nInteractive mode. Commands:")
            print("  param <path> <value>  - Update parameter")
            print("  status                - Get session status")
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
                    elif parts[0] == "param" and len(parts) >= 3:
                        param_path = parts[1]
                        value = float(parts[2]) if "." in parts[2] else int(parts[2])
                        await client.update_parameter(param_path, value)
                    elif parts[0] == "status":
                        await client.get_status()
                    elif parts[0] == "geometry":
                        url = client.get_last_geometry_url()
                        print(f"Geometry URL: {url if url else 'None'}")
                    else:
                        print("Unknown command")
                
                except EOFError:
                    break
        
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
