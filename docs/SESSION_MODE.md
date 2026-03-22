# Aurora Session - Quick Start Guide

This guide explains how to set up and use the Aurora Session system, which allows users to manipulate Houdini scenes in real-time through a web browser.

## Architecture Overview

The Aurora Session system uses a **pure WebSocket architecture** for real-time, bidirectional communication:

1. **WebSocket API Gateway** - Single communication endpoint
2. **Lambda Functions** - Route messages and manage EC2 lifecycle
3. **DynamoDB** - Track active sessions and connection mappings
4. **EC2 Instances** - Run Houdini with WebSocket client
5. **Web Client** - Browser-based interface or Python client

### Message Flow

```
Browser                Lambda               EC2 (Houdini)
   |                      |                      |
   |--connect------------>|                      |
   |<-session_id----------|                      |
   |                      |                      |
   |--start_session------>|--launch EC2-------->|
   |                      |                      |
   |                      |<---connect-----------|
   |                      | (routes connections) |
   |                      |                      |
   |--update_param------->|--forward----------->|
   |                      |                      |--(Houdini)
   |<--geometry-----------|<---geometry---------|
```

**Key advantages:**
- ⚡ **~50ms latency** - No polling, true real-time
- 💰 **Lower cost** - No SQS charges, no wasted polls
- 🎯 **Simple** - Direct message routing
- 🔄 **Bidirectional** - Native WebSocket semantics

## Deployment

### 1. Deploy Infrastructure

Enable Aurora Session mode in Terraform:

```bash
cd infra/provisioning/deployment

# Deploy with Aurora Session mode enabled
terraform apply -var="enable_session_mode=true"
```

**Important Outputs:**
After deployment, note these values (saved to `samples/tf_outputs.json`):
- `websocket_url` - Your WebSocket API Gateway endpoint (e.g., `wss://xxxxx.execute-api.eu-north-1.amazonaws.com/production`)
- `sessions_table_name` - DynamoDB table name for session tracking
- `session_launch_template_name` - Dedicated launch template for Aurora Session instances

### 2. Infrastructure Components

The deployment automatically creates:

- **WebSocket API Gateway** with `$connect`, `$disconnect`, and `$default` routes
- **Three Lambda functions** for connection handling, disconnection, and message routing
- **DynamoDB table** with GSI for session tracking by connection ID
- **Dedicated launch template** (`aurora-session-*`) that runs `runtime/session/entrypoint.sh`
- **CloudWatch log groups** for API Gateway and Lambda debugging
- **IAM roles** with proper permissions for EC2 launch, DynamoDB access, and WebSocket management

**Note:** The Aurora Session launch template is separate from the batch processing template and automatically runs the correct entrypoint script.

## Usage

### Web Browser Interface (Primary Method)

The system includes a fully functional web-based interface for interactive Houdini sessions.

#### Setup

1. **Configure WebSocket URL:**
   - Edit `webapp/config.js` with your WebSocket endpoint:
   ```javascript
   const CONFIG = {
       websocket_url: "wss://your-api-id.execute-api.region.amazonaws.com/production",
       idle_timeout_minutes: 15,  // Session auto-terminates after inactivity
       idle_warning_minutes: 2,   // Warning appears before timeout
       region: "eu-north-1",
       environment: "prod"
   };
   ```

2. **Open the webapp:**
   - Open `webapp/session_tool_demo.html` in your browser
   - Or serve it via a local web server:
   ```bash
   cd webapp
   python -m http.server 8000
   # Visit http://localhost:8000/session_tool_demo.html
   ```

#### Workflow

1. **Select HIP File**: Use the file browser to select a `.hip` or `.hda` file from your local machine
2. **Initialize Session**: Click "Initialize Session" button
   - File is uploaded directly to S3 using presigned URLs (no size limits)
   - WebSocket connection is established
   - EC2 instance is launched with your file (takes 1-2 minutes)
3. **View Parameters**: Parameters are automatically extracted and displayed
   - Dynamic UI generated for all parameter types
   - Sliders, checkboxes, dropdowns, color pickers, etc.
4. **Interact**: Adjust parameters using controls
   - Geometry updates automatically in the 3D viewer
   - Changes are sent only when slider is released (not while dragging)
   - Activity resets the idle timeout timer
5. **Monitor Session**: Status and geometry info displayed in real-time
   - Point/primitive counts
   - Session status (connected, idle warning, terminated)
6. **Terminate**: Click "Terminate Session" when done to clean up resources
   - Or session auto-terminates after configured idle period

**Idle Timeout:**
Sessions automatically terminate after inactivity to save costs:
- Default: 15 minutes of no parameter changes
- Warning: Alert appears 2 minutes before termination
- Configurable in `webapp/config.js`
- Any parameter interaction resets the timer

#### Features

- **🎨 Modern UI**: Clean interface with loading states and status updates
- **📁 File Upload**: Direct browser → S3 upload with presigned URLs (no size limits)
- **🎭 3D Viewer**: Aurora Session model viewer with orbit controls, zoom, and auto-rotate
- **🎛️ Dynamic Parameters**: Automatically generated UI for all parameter types
  - Float sliders with live value display
  - Integer inputs
  - Checkboxes
  - Dropdowns/menus
  - Text inputs (single and multiline)
  - Color pickers
  - File browsers
- **📊 Geometry Info**: Real-time point and primitive counts
- **💾 Session Management**: Clear session status and termination controls
- **🔄 Auto-reload**: Geometry updates automatically when parameters change

### Option 2: Python Client (programmatic API)

```python
import asyncio
from samples.session_tool_client import AuroraSessionClient

async def main():
    # Create client
    client = AuroraSessionClient(
        websocket_url="wss://your-api-id.execute-api.us-east-1.amazonaws.com/production"
    )
    
    # Connect and specify HDA file
    await client.connect(hda_file="MyTool.hda")
    
    # Start background message receiver
    receive_task = asyncio.create_task(
        client.receive_messages()
    )
    
    # Start the session
    await client.start_session()
    
    # Update parameters
    await client.update_parameter("/obj/CONTAINER/user_hda/size", 5.0)
    await client.update_parameter("/obj/CONTAINER/user_hda/height", 2.0)
    
    # Get geometry URL
    geometry_url = client.get_last_geometry_url()
    print(f"Geometry: {geometry_url}")
    
    # Terminate
    await client.terminate()

asyncio.run(main())
```

### Option 3: Aurora Session CLI (debugging)

The Python client automatically reads the WebSocket URL from `samples/tf_outputs.json`:

```bash
# No need to specify --websocket-url if tf_outputs.json exists
python samples/session_tool_client.py --command interactive

# Or specify explicitly
python samples/session_tool_client.py \
  --websocket-url "wss://your-api-id.execute-api.eu-north-1.amazonaws.com/production" \
  --hda-file "MyTool.hda" \
  --command interactive

# Then use commands:
>>> param /obj/geo1/transform1/tx 5.0
>>> param /obj/geo1/transform1/ty 2.0
>>> status
>>> geometry
>>> quit
```

## Current Implementation Status

### ✅ Completed (Phase 1 - MVP)

**End-to-End Workflow:**
- ✅ Complete browser-to-Houdini pipeline functional
- ✅ File upload → EC2 launch → parameter interaction → geometry display
- ✅ All WebSocket communication working bidirectionally
- ✅ Session lifecycle management (create, monitor, terminate)

**Frontend (Web Interface):**
- ✅ Modern responsive UI with landing page and main application view
- ✅ File browser for selecting HIP/HDA files
- ✅ File upload to S3 via presigned URLs (handles files of any size)
- ✅ 3D geometry viewer using `<model-viewer>` web component
  - Interactive orbit controls (rotate, pan, zoom)
  - Auto-rotation until user interaction
  - Environment lighting and shadows
- ✅ Dynamic parameter UI generation from JSON schema
  - All 7 parameter types supported (float, int, checkbox, menu, string, color, file)
  - Optimized slider updates (send only on mouse release)
  - Real-time value display
- ✅ Session controls (initialize, terminate)
- ✅ Loading states with progress messages
- ✅ Geometry info overlay (point/primitive counts)
- ✅ Error handling and user feedback
- ✅ Idle timeout warnings and notifications

**Backend (Infrastructure):**
- ✅ S3 CORS configuration for browser uploads
- ✅ Presigned URL generation in Lambda
- ✅ WebSocket message routing (browser ↔ Lambda ↔ EC2)
- ✅ Session-based S3 paths (`{session_id}/{filename}`)
- ✅ IAM permissions for S3 access
- ✅ EC2 instance tagging with session metadata
- ✅ Automatic file download from S3 to EC2

**Communication:**
- ✅ WebSocket bidirectional messaging
- ✅ Session management with DynamoDB
- ✅ EC2 lifecycle management (launch, terminate)
- ✅ Message routing between browser and EC2
- ✅ Connection identification and session mapping

**Session Management:**
- ✅ Idle timeout detection and auto-termination
- ✅ Configurable timeout and warning periods (via `config.js`)
- ✅ Activity tracking (resets on parameter changes)
- ✅ User warnings before timeout
- ✅ Graceful cleanup on timeout or manual termination

**Testing:**
- ✅ Full workflow tested with real HDA file uploads and parameter interaction

**Current Status:**
The Aurora Session system is **complete and functional** with real Houdini processing. The system:
- Uploads HDA files from browser to S3
- Launches EC2 instances with proper configuration
- Downloads HDA from S3, installs it into session_runner.hip
- Extracts real parameter schemas from the HDA for dynamic UI generation
- Processes parameter updates through Houdini and exports GLTF geometry
- Establishes bidirectional WebSocket communication via two-process architecture
- Displays parameters and 3D geometry in the browser
- Manages session lifecycle including idle timeouts

## File Upload Protocol

The system uses a secure, efficient file upload flow:

```
Browser                 Lambda (WebSocket)         S3
   │                           │                    │
   │──request_upload_url──────>│                    │
   │   {action, filename}      │                    │
   │                           │                    │
   │<──upload_url_ready────────│                    │
   │   {upload_url, s3_key}    │                    │
   │                           │                    │
   │────PUT file────────────────────────────────────>│
   │           (presigned URL)                      │
   │                           │                    │
   │<───200 OK──────────────────────────────────────│
```

**Benefits:**
- **No size limits**: File bypasses Lambda and goes directly to S3
- **Secure**: Presigned URLs expire after 15 minutes
- **Fast**: Direct browser → S3 upload
- **Organized**: Files stored at `{session_id}/{filename}` for easy cleanup

## Configuration

### Idle Timeout Settings

Configure session timeout behavior in `webapp/config.js`:

```javascript
const CONFIG = {
    websocket_url: "wss://your-api-id.execute-api.region.amazonaws.com/production",
    
    // Idle timeout in minutes (default: 15)
    // Session auto-terminates after this period of inactivity
    idle_timeout_minutes: 15,
    
    // Warning time before timeout in minutes (default: 2)
    // Alert appears this long before auto-termination
    idle_warning_minutes: 2,
    
    region: "eu-north-1",
    environment: "prod"
};
```

**How It Works:**
1. Timer starts when EC2 instance connects
2. Any parameter change resets the timer
3. Warning appears at `(timeout - warning)` minutes
4. Session terminates at `timeout` minutes
5. EC2 instance automatically shuts down to save costs

**Examples:**

```javascript
// Quick testing (5 min timeout, 1 min warning)
idle_timeout_minutes: 5,
idle_warning_minutes: 1,

// Standard sessions (15 min timeout, 2 min warning) - DEFAULT
idle_timeout_minutes: 15,
idle_warning_minutes: 2,

// Long work sessions (30 min timeout, 5 min warning)
idle_timeout_minutes: 30,
idle_warning_minutes: 5,

// Extended sessions (60 min timeout, 10 min warning)
idle_timeout_minutes: 60,
idle_warning_minutes: 10,
```

**Cost Savings:**
Idle timeout prevents forgotten sessions from running indefinitely. A t3.medium instance costs ~$0.05/hour, so a 15-minute timeout saves ~$0.0125 per forgotten session compared to requiring manual cleanup.

## Houdini File Requirements

Your Houdini file should be structured for interactive use:

1. **Location**: Place HIP files in `SHARED/` folder or provide S3 path
2. **Output Node**: Tag a geometry node as "output" or ensure `/obj/output` exists
3. **Exposed Parameters**: Parameters should have full paths like `/obj/geo1/transform1/tx`

Example structure:
```
/obj
  └── geo1
      ├── transform1 (Transform SOP)
      │   ├── tx (translate X)
      │   ├── ty (translate Y)
      │   └── tz (translate Z)
      └── output (final geometry node)
```

## Message Protocol

### Client → Server

**Request Upload URL:**
```json
{
  "action": "request_upload_url",
  "filename": "my_scene.hip",
  "content_type": "application/octet-stream"
}
```

**Start Session:****
```json
{
  "action": "start_session"
}
```

**Update Parameter:**
```json
{
  "action": "update_parameter",
  "param": "/obj/geo1/transform1/tx",
  "value": 5.0
}
```

**Get Status:**
```json
{
  "action": "get_status"
}
```

**Terminate:**
```json
{
  "action": "terminate_session"
}
```

### Server → Client

**Upload URL Ready:**
```json
{
  "action": "upload_url_ready",
  "session_id": "uuid",
  "upload_url": "https://bucket.s3.amazonaws.com/...",
  "s3_key": "session-id/filename.hip",
  "bucket": "aurora-input-bucket"
}
```

**Session Started:**
```jsonDefault is `t3.large` (~$0.08/hr) for testing; use `g4dn.2xlarge` (~$0.75/hr) for GPU-accelerated Houdini rendering
- **Spot Instances**: Consider using Spot instances for development (60-90% cost savings)
- **Session Cleanup**: Implement idle timeout detection to terminate inactive sessions

**Cost Breakdown (per hour):**
- EC2 (t3.large): ~$0.08
- API Gateway: $1.00 per million messages (negligible for interactive use)
- DynamoDB: Pay-per-request (negligible for session tracking)
- Lambda: $0.20 per million requests (negligible)
- S3 storage: ~$0.023 per GB-month

**Total: ~$0.08-0.10/hour** for testing without GPU
  "action": "session_started",
  "session_id": "uuid",
  "instance_id": "i-xxxxx",
  "status": "starting"
}
```

**Parameters Ready:**
```json
{
  "action": "parameters_ready",
  "session_id": "uuid",
  "parameters": {
    "tool_name": "My Tool",
    "parameters": {
      "/obj/geo1/transform1/tx": {
        "name": "Translate X",
        "type": "float",
        "default": 0.0,
        "ui": {
          "min": -10.0,
          "max": 10.0,
          "step": 0.1,
          "label": "Translation X"
        }
      }
    },
    "geometry_output": {
      "primary_node": "/obj/OUT",
      "format": "gltf"
    }
  }
}
```

**Ready:**
```json
{
  "status": "ready",
  "message": "Houdini session is ready"
}
```

**Geometry Update:**
```json
{
  "action": "geometry_ready",
  "geometry": {
    "url": "https://s3.amazonaws.com/.../output.glb",
    "point_count": 14556,
    "primitive_count": 15452
  }
}
```

## Cost Optimization

Aurora Sessions consume compute resources while active. The system includes several cost-saving features:

**Automatic Idle Timeout (Implemented):**
- Sessions auto-terminate after configured period of inactivity (default: 15 minutes)
- Warning appears before termination to give users a chance to continue
- Prevents forgotten sessions from running indefinitely
- Configurable per-deployment via `webapp/config.js`
- Estimated savings: ~$0.01-0.02 per prevented forgotten session

**Best Practices:**
- Terminate sessions promptly when done (via Terminate button)
- Adjust `idle_timeout_minutes` based on typical workflow duration
- Use smaller instance types (t3.small, t3.medium) for simple parameter exploration
- Reserve larger instances (c5.xlarge+) for actual Houdini cooking once implemented
- Monitor CloudWatch metrics to optimize instance sizing
- Consider spot instances for non-critical workloads (future enhancement)

**Cost Breakdown (Approximate):**
- **EC2 Instance**: ~$0.04-0.08/hour (t3.medium in eu-north-1)
- **WebSocket API**: $1.00/million messages + $0.25/million connection minutes
- **S3 Storage**: $0.023/GB/month
- **Data Transfer**: $0.09/GB out (geometry download)
- **Lambda**: Negligible (free tier covers typical usage)

**Example Session Cost:**
- 10-minute session with t3.medium
- Upload 2MB HIP file, download 5MB geometry 3 times
- Cost: ~$0.01-0.02 per session

## Troubleshooting

### File Upload Fails with CORS Error

**Error:** `Access-Control-Allow-Origin header is missing`

**Solution:** Ensure S3 bucket CORS is configured. This should be automatic with Terraform, but verify:
```bash
terraform apply  # Re-apply to ensure CORS resources are created
```

The CORS configuration allows:
- Methods: `PUT`, `POST`, `GET`
- Origins: `*` (all origins)
- Headers: `*` (all headers)

### Lambda Function Errors

**Check:** CloudWatch logs at `/aws/lambda/aurora-session-message`
2. Verify launch template exists: `aurora-session-*`
3. Ensure IAM roles have necessary permissions (check for `UnauthorizedOperation` errors)
4. Verify security group allows outbound HTTPS (port 443) for WebSocket

### Messages Not Routing

1. Check API Gateway logs: `/aws/apigateway/aurora-session`
2. Verify Lambda has environment variables: `LAUNCH_TEMPLATE_NAME`, `WEBSOCKET_API_ENDPOINT`
3. Check DynamoDB table for session entry with correct `browser_connection_id` and `ec2_connection_id`
4. Look for "Session not found" errors indicating connection ID mismatch

**Common Fix:** EC2 messages include `session_id` in body, browser messages use `connection_id` GSI lookup

### EC2 Instance Fails to Start

1. Check CloudWatch logs: `/aws/ec2/aurora-session`
2. Verify git repository is accessible (check PAT in Secrets Manager)
3. Look for errors in entrypoint script (`DATA_ROOT` unbound, missing env vars)
4. Check instance has proper IAM role for S3, DynamoDB, and EC2 tags access

### WebSocket Connection Errors

**HTTP 429 (Too Many Requests):**
- Wait 1-2 minutes for API Gateway throttle limits to reset
- Check API Gateway stage throttle settings (default: 500 burst, 100 rate)

**HTTP 403 (Forbidden):**
- Verify API Gateway routes are deployed
- Check Lambda permissions allow API Gateway invocation

**Connection Timeout:**
- Verify API Gateway endpoint URL is correct
- Check network/firewall allows WebSocket connections (wss://)

### Geometry Not Loading

1. Check browser console for loading errors
2. Verify presigned URL is valid (check Lambda logs)
3. Ensure model-viewer can access the geometry URL
4. Check S3 bucket CORS configuration for GET requests
5. Verify geometry file format is glTF or GLB

### WebSocket Disconnects

1. Check API Gateway logs
2. Verify Lambda timeout is sufficient (30s recommended)
3. EC2 may have lost connection - check network stability
4. Ensure WebSocket endpoint URL is correct

## Roadmap

### Short Term
1. ⬜ Parameter auto-discovery from HIP file (introspection)
2. ⬜ Session persistence (reconnect to existing sessions)
3. ⬜ Export final geometry downloads (non-preview)
4. ⬜ Handle complex parameter types (ramps, multiparms)

## Security Considerations

- **API Gateway Authorization**: Add Cognito or Lambda authorizer for production
- **WebSocket Authentication**: Pass auth tokens in connection query string
- **S3 Access**: Use presigned URLs (already implemented)
- **Network Isolation**: EC2 instances should be in private subnets for production
- **Secrets**: Houdini credentials are pulled from AWS Secrets Manager

## Support

For issues or questions:
1. Check CloudWatch logs for Lambda (`/aws/lambda/aurora-session-*`)
2. Check API Gateway logs (`/aws/apigateway/aurora-session`)
3. Check EC2 logs (`/aws/ec2/aurora-session`)
4. Review DynamoDB session table for connection mappings
5. Check this repository's issues on GitHub
