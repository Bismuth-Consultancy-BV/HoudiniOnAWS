"""
Aurora Session WebSocket handler - manages connection to API Gateway.
This runs in a pure Python asyncio process (NO hou imports).
Forwards messages between browser clients and local Houdini runner.
"""

import json
import logging
import os
import sys
import time
import asyncio
import websockets
from typing import Optional, Set

# Setup logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


class WebSocketBridge:
    """Bridges API Gateway WebSocket with local Houdini runner."""

    def __init__(self, session_id: str, websocket_url: str, local_port: int = 7007):
        self.session_id = session_id
        self.websocket_url = websocket_url
        self.local_port = local_port
        self.api_gateway_ws: Optional[websockets.WebSocketClientProtocol] = None
        self.houdini_clients: Set[websockets.WebSocketServerProtocol] = set()
        self.running = True
        self.ready = False

        logger.info(f"Initializing WebSocket bridge for session {session_id}")
        logger.info(f"API Gateway URL: {websocket_url}")
        logger.info(f"Local port: {local_port}")

    async def connect_to_api_gateway(self):
        """Connect to AWS API Gateway WebSocket."""
        url_with_session = f"{self.websocket_url}?session_id={self.session_id}"

        logger.info(f"=== API GATEWAY CONNECTION START ===")
        logger.info(f"Connecting to: {url_with_session}")

        max_retries = 5
        retry_delay = 2

        for attempt in range(max_retries):
            try:
                connect_start = time.time()
                self.api_gateway_ws = await websockets.connect(
                    url_with_session, ping_interval=30, ping_timeout=10
                )
                connect_time = time.time() - connect_start
                logger.info(f"Connected to API Gateway WebSocket in {connect_time:.2f}s")
                logger.info("=== API GATEWAY CONNECTION COMPLETE ===")

                # Send initial ready status once Houdini is connected
                return True

            except Exception as e:
                logger.error(
                    f"Connection attempt {attempt + 1}/{max_retries} failed: {e}"
                )
                if attempt < max_retries - 1:
                    logger.info(f"Retrying in {retry_delay}s...")
                    await asyncio.sleep(retry_delay)
                else:
                    logger.error("All connection attempts failed")
                    return False

        return False

    async def send_to_browser(self, message: dict):
        """Send message to browser via API Gateway."""
        try:
            if self.api_gateway_ws:
                message_with_metadata = {
                    "session_id": self.session_id,
                    "timestamp": time.time(),
                    **message,
                }
                await self.api_gateway_ws.send(json.dumps(message_with_metadata))
                logger.debug(
                    f"Sent to browser: {message.get('status') or message.get('action')}"
                )
        except Exception as e:
            logger.error(f"Error sending to browser: {e}")

    async def handle_houdini_client(
        self, websocket
    ):
        """Handle connection from local Houdini runner."""
        logger.info("=== HOUDINI RUNNER CONNECTED ===")
        self.houdini_clients.add(websocket)

        try:
            # Send ready notification to browser when Houdini connects
            if not self.ready:
                self.ready = True
                logger.info("Sending 'ready' status to browser...")
                await self.send_to_browser(
                    {"status": "ready", "message": "Houdini session is ready"}
                )
                logger.info("Ready notification sent to browser")

            # Forward messages from Houdini to browser
            async for message in websocket:
                try:
                    data = json.loads(message)
                    action = data.get("action") or data.get("status")
                    logger.info(f"Received from Houdini: {action}")

                    # Log geometry data if present (check nested structure)
                    if "geometry" in data and isinstance(data["geometry"], dict):
                        geom = data["geometry"]
                        if "url" in geom or "geometry_url" in geom:
                            url = geom.get("url") or geom.get("geometry_url")
                            logger.info(f"  → Geometry URL: {url[:80]}...")
                        if "point_count" in geom:
                            logger.info(
                                f"  → Points: {geom['point_count']}, Primitives: {geom.get('primitive_count')}"
                            )

                    await self.send_to_browser(data)
                    logger.info(f"Forwarded to browser successfully")
                except json.JSONDecodeError as e:
                    logger.error(f"Invalid JSON from Houdini: {e}")

        except websockets.exceptions.ConnectionClosed:
            logger.info("Houdini runner disconnected")
        finally:
            self.houdini_clients.discard(websocket)
            self.ready = False

    async def start_local_server(self):
        """Start local WebSocket server for Houdini runner."""
        logger.info(f"=== STARTING LOCAL WEBSOCKET SERVER ===")
        logger.info(f"Binding to 127.0.0.1:{self.local_port}")

        async with websockets.serve(
            self.handle_houdini_client,
            "127.0.0.1",
            self.local_port,
            ping_interval=30,
            ping_timeout=10,
        ):
            logger.info(
                f"Local WebSocket server ready on ws://127.0.0.1:{self.local_port}"
            )
            logger.info("Waiting for Houdini runner to connect...")
            # Keep server running
            await asyncio.Future()

    async def forward_browser_to_houdini(self):
        """Forward messages from browser to Houdini runner."""
        logger.info("Starting browser message forwarder")

        try:
            while self.running and self.api_gateway_ws:
                try:
                    # Wait for message from browser
                    message = await asyncio.wait_for(
                        self.api_gateway_ws.recv(), timeout=1.0
                    )

                    # Parse command
                    try:
                        command = json.loads(message)
                        action = command.get("action")
                        logger.info(f"Received from browser: {action}")

                        # Handle terminate action
                        if action == "terminate":
                            self.running = False
                            # Notify Houdini to shut down
                            for client in list(self.houdini_clients):
                                try:
                                    await client.send(message)
                                except:
                                    pass
                            break

                        # Forward to all connected Houdini clients
                        if self.houdini_clients:
                            for client in list(self.houdini_clients):
                                try:
                                    await client.send(message)
                                    logger.debug(f"Forwarded to Houdini: {action}")
                                except Exception as e:
                                    logger.error(f"Error forwarding to Houdini: {e}")
                                    self.houdini_clients.discard(client)
                        else:
                            # No Houdini runner connected
                            await self.send_to_browser(
                                {
                                    "error": "Houdini runner not connected",
                                    "action": action,
                                }
                            )

                    except json.JSONDecodeError as e:
                        logger.error(f"Invalid JSON from browser: {e}")
                        await self.send_to_browser({"error": "Invalid JSON"})

                except asyncio.TimeoutError:
                    # No message, continue loop
                    continue

                except websockets.exceptions.ConnectionClosed:
                    logger.info("API Gateway connection closed")
                    self.running = False
                    break

        except Exception as e:
            logger.error(f"Error in browser message forwarder: {e}")
            self.running = False

        finally:
            logger.info("Browser message forwarder stopped")

    async def run(self):
        """Main run loop for the bridge."""
        # Connect to API Gateway
        if not await self.connect_to_api_gateway():
            logger.error("Failed to connect to API Gateway. Exiting.")
            return False

        # Start local server and browser forwarder concurrently
        try:
            await asyncio.gather(
                self.start_local_server(), self.forward_browser_to_houdini()
            )
        except Exception as e:
            logger.error(f"Error in bridge: {e}")
        finally:
            # Clean up
            if self.api_gateway_ws:
                await self.api_gateway_ws.close()

            # Close all Houdini connections
            for client in list(self.houdini_clients):
                try:
                    await client.close()
                except:
                    pass

        logger.info("WebSocket bridge stopped")
        return True


async def main():
    """Main entry point for WebSocket handler."""

    # Get configuration from environment
    session_id = os.getenv("SESSION_ID")
    websocket_url = os.getenv("WEBSOCKET_URL")
    local_port = int(os.getenv("LOCAL_WS_PORT", "7007"))

    if not all([session_id, websocket_url]):
        logger.error("Missing required configuration. Cannot start.")
        logger.error(f"SESSION_ID: {session_id}")
        logger.error(f"WEBSOCKET_URL: {websocket_url}")
        sys.exit(1)

    logger.info(f"Starting WebSocket bridge for session: {session_id}")
    logger.info(f"API Gateway URL: {websocket_url}")
    logger.info(f"Local port: {local_port}")

    # Create and run bridge
    bridge = WebSocketBridge(
        session_id=session_id, websocket_url=websocket_url, local_port=local_port
    )

    try:
        await bridge.run()
    except KeyboardInterrupt:
        logger.info("Received interrupt signal")
    finally:
        logger.info("WebSocket handler ended")


if __name__ == "__main__":
    # Run the async main function
    asyncio.run(main())
