# Aurora Session: Two-Process Architecture

## Problem

Houdini's `hython` interpreter doesn't support `asyncio` event loops properly. Attempting to use `asyncio.run()` or async/await patterns with `import hou` in the same process causes compatibility issues.

## Solution

Following the pattern from `third_party/HythonViser`, we split the Aurora Session mode into **two separate processes** that communicate via local WebSocket.

## Architecture

```
Browser                API Gateway           EC2 Instance
  |                    WebSocket                  |
  |                       |                       |
  |---- messages -------->|                       |
  |                       |                       |
  |                       |    Process 1: WebSocket Handler
  |                       |    (Pure Python + asyncio)
  |                       |<---- connects ------->|
  |                       |                       |
  |                       |                       | ws://localhost:7007
  |                       |                       |
  |                       |    Process 2: Houdini Runner
  |                       |    (hython + hou module)
  |                       |<----------------------|
  |                       |                       |
  |<---- responses -------|                       |
```

### Process 1: WebSocket Handler
**File:** `runtime/session/websocket_handler.py`

**Role:** Bridge between API Gateway and local Houdini runner
- Runs standard Python with `asyncio.run()`
- **Does NOT import hou**
- Connects to API Gateway WebSocket as client
- Starts local WebSocket server on port 7007
- Forwards messages bidirectionally

**Key Features:**
- Full async/await support
- Handles connection retries and errors
- Manages client lifecycle
- No Houdini dependencies

### Process 2: Houdini Runner
**File:** `runtime/session/houdini_runner.py`

**Role:** Execute Houdini operations
- Runs with `hython` (Houdini Python)
- **Imports hou module**
- Connects as WebSocket client to localhost:7007
- Processes Houdini commands synchronously
- Returns results via WebSocket

**Key Features:**
- Minimal asyncio usage (only for WebSocket I/O)
- All Houdini operations are synchronous
- No async/await in command processing
- Clean separation of concerns

## Communication Protocol

### Messages from Browser → Houdini

Commands are JSON objects forwarded through the handler:

```json
{
  "action": "update_param",
  "param": "/obj/geo1/mountain1/height",
  "value": 5.0
}
```

### Messages from Houdini → Browser

Responses are JSON objects with results:

```json
{
  "status": "success",
  "param": "/obj/geo1/mountain1/height",
  "old_value": 3.0,
  "new_value": 5.0,
  "geometry": {
    "geometry_url": "https://s3.../geometry.obj",
    "point_count": 1024,
    "primitive_count": 512
  }
}
```

## Startup Sequence

1. **WebSocket Handler starts** (`python websocket_handler.py`)
   - Connects to API Gateway WebSocket
   - Starts local server on port 7007
   - Waits for Houdini runner

2. **Houdini Runner starts** (`hython houdini_runner.py`)
   - Loads HIP file using hou module
   - Connects to localhost:7007
   - Sends ready notification

3. **Handler forwards ready** to browser
   - Session is now active

## Benefits

### ✅ Compatibility
- No asyncio/Hython conflicts
- Each process uses appropriate runtime

### ✅ Separation of Concerns
- Network I/O isolated from Houdini operations
- Clear interfaces between components

### ✅ Resilience
- Processes can be restarted independently
- Connection failures isolated

### ✅ Maintainability
- Easier to debug
- Can test components separately
- Follows established pattern (HythonViser)

## Environment Variables

Both processes share configuration via environment:

```bash
SESSION_ID=abc-123              # Unique session identifier
WEBSOCKET_URL=wss://...         # API Gateway WebSocket URL
S3_OUTPUT_BUCKET=my-bucket      # S3 bucket for geometry exports
HIP_FILE=/path/to/file.hip      # Houdini file to load
LOCAL_WS_PORT=7007              # Local WebSocket port (default)
```

## Deployment

The `runtime/session/entrypoint.sh` script manages both processes:

```bash
# Start handler
python websocket_handler.py &
HANDLER_PID=$!

# Wait for handler to start
sleep 3

# Start runner
hython houdini_runner.py &
RUNNER_PID=$!

# Wait for any to exit
wait -n $HANDLER_PID $RUNNER_PID
```

## Comparison with HythonViser

This architecture directly mirrors [HythonViser](../third_party/HythonViser/):

| HythonViser | HoudiniOnAWS |
|-------------|--------------|
| `start_viewer.py` (viser + asyncio) | `websocket_handler.py` |
| `start_runner.py` (hython + websockets) | `houdini_runner.py` |
| Local WebSocket (port 7007) | Local WebSocket (port 7007) |
| Browser ← viser → viewer → ws → runner | Browser ← API GW → handler → ws → runner |

## References

- HythonViser example: `third_party/HythonViser/`
- Entrypoint script: `runtime/session/entrypoint.sh`
