#!/bin/bash -u
# Entrypoint for Aurora Session workflow on EC2

# Helper function to print timestamped logs
log_step() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S.%3N')] $1"
}

log_step "==================== ENTRYPOINT START ===================="

kill_houdini() {
    echo "Terminating Houdini license..."
    /opt/houdini/bin/hserver --blocking-quit
    echo "Houdini License Released"
    echo ""
}

# Cleanup function on complete or error
cleanup() {
    INSTANCE_ID=$(curl -s http://169.254.169.254/latest/meta-data/instance-id)

    kill_houdini    
    
    ############################################################################################
    echo "Cleaning up the instance..."
    # Signal termination to the Auto Scaling group
    sleep 5 # Let the logs finish uploading to Cloudwatch
    aws ec2 terminate-instances --instance-ids "$INSTANCE_ID" --region "$AWS_REGION"
    ############################################################################################
}

trap cleanup EXIT

START_TIME=$(date +%s)

log_step "Setting up environment..."
# Set up the environment
source /etc/environment  # Get AURORA_TOOLING_ROOT and other system vars
# source /opt/miniconda/etc/profile.d/conda.sh
# conda activate aurora_env
# log_step "Python version: $(python --version)"

log_step "=========================================="
log_step "AURORA SESSION STARTING"
log_step "=========================================="

# ============================================================
# OPTIMIZATION: Launch hython IMMEDIATELY to start its ~90s cold boot
# in parallel with metadata retrieval, S3 download, and licensing.
# The runner script polls for a ready-signal file before loading the HIP.
# ============================================================
export HYTHON_READY_SIGNAL="/tmp/houdini_boot_ready"
rm -f "$HYTHON_READY_SIGNAL"

log_step "EARLY LAUNCH: Starting hython cold boot NOW (runs in parallel with init)..."
cd "${AURORA_TOOLING_ROOT:-/opt/aurora}"
/opt/houdini/bin/hython "$AURORA_TOOLING_ROOT/runtime/session/houdini_runner.py" &
HOUDINI_RUNNER_PID=$!
log_step "hython launched (PID: $HOUDINI_RUNNER_PID) — warming up while we do licensing + downloads"

# ============================================================
# While hython is cold-starting (~90s), do ALL other init work
# ============================================================
log_step "Retrieving instance metadata..."
INSTANCE_ID=$(curl -s http://169.254.169.254/latest/meta-data/instance-id)
AWS_REGION=$(curl -s http://169.254.169.254/latest/meta-data/placement/region)

log_step "Instance ID: $INSTANCE_ID"
log_step "AWS Region: $AWS_REGION"

# Retrieve all tags associated with this instance
log_step "Retrieving instance tags..."
TAGS_JSON=$(aws ec2 describe-tags --region "$AWS_REGION" --filters "Name=resource-id,Values=$INSTANCE_ID" --output json)

log_step "Instance Tags:"
echo "$TAGS_JSON" | jq .

# Extract relevant tags
log_step "Extracting configuration from tags..."
SESSION_ID=$(echo "$TAGS_JSON" | jq -r '.Tags[] | select(.Key=="session_id") | .Value')
WEBSOCKET_URL=$(echo "$TAGS_JSON" | jq -r '.Tags[] | select(.Key=="websocket_url") | .Value')
INPUT_BUCKET=$(echo "$TAGS_JSON" | jq -r '.Tags[] | select(.Key=="input_bucket") | .Value')
IDLE_TIMEOUT_SECONDS=$(echo "$TAGS_JSON" | jq -r '.Tags[] | select(.Key=="idle_timeout_seconds") | .Value')
IDLE_WARNING_SECONDS=$(echo "$TAGS_JSON" | jq -r '.Tags[] | select(.Key=="idle_warning_seconds") | .Value')
S3_OUTPUT_BUCKET=$(echo "$TAGS_JSON" | jq -r '.Tags[] | select(.Key=="s3_output_bucket") | .Value')

log_step "=========================================="
log_step "Session Configuration:"
log_step "  Session ID: $SESSION_ID"
log_step "  Input Bucket: $INPUT_BUCKET"
log_step "  WebSocket URL: $WEBSOCKET_URL"
log_step "  Idle Timeout: $IDLE_TIMEOUT_SECONDS seconds"
log_step "=========================================="

# Set environment variables for the daemon
export SESSION_ID="$SESSION_ID"
export WEBSOCKET_URL="$WEBSOCKET_URL"
export AWS_REGION="$AWS_REGION"
export INPUT_BUCKET="$INPUT_BUCKET"

# Set defaults for idle timeout (900 seconds / 15 minutes)
if [ -z "$IDLE_TIMEOUT_SECONDS" ] || [ "$IDLE_TIMEOUT_SECONDS" = "null" ]; then
    IDLE_TIMEOUT_SECONDS="900"
fi
export IDLE_TIMEOUT_SECONDS

# Set defaults for idle warning (120 seconds / 2 minutes)
if [ -z "$IDLE_WARNING_SECONDS" ] || [ "$IDLE_WARNING_SECONDS" = "null" ]; then
    IDLE_WARNING_SECONDS="120"
fi
export IDLE_WARNING_SECONDS

# Set default for S3 output bucket
if [ -z "$S3_OUTPUT_BUCKET" ] || [ "$S3_OUTPUT_BUCKET" = "null" ]; then
    S3_OUTPUT_BUCKET="aurora-output-bucket"
fi
export S3_OUTPUT_BUCKET

log_step "S3 Output Bucket: $S3_OUTPUT_BUCKET"
log_step "Idle Timeout: $IDLE_TIMEOUT_SECONDS seconds ($(($IDLE_TIMEOUT_SECONDS/60)) minutes)"
log_step "Idle Warning: $IDLE_WARNING_SECONDS seconds ($(($IDLE_WARNING_SECONDS/60)) minutes before timeout)"

# Set DATA_ROOT if not already set (for file resolution)
# Use default parameter expansion to avoid "set -u" errors
export DATA_ROOT="${DATA_ROOT:-$AURORA_TOOLING_ROOT/SHARED}"

# Session HIP file (the pre-made template with EXPORT and CONTAINER nodes)
export SESSION_HIP="$AURORA_TOOLING_ROOT/runtime/session/session_runner.hip"

# Clean up previous runs (if any) and create fresh directory
log_step "Preparing workspace at $DATA_ROOT..."
sudo rm -rf "$AURORA_TOOLING_ROOT"/SHARED
mkdir -p "$AURORA_TOOLING_ROOT"/SHARED
log_step "Workspace ready"

# No HDA at boot — user will load one via Session > Load HDA in the webapp.
log_step "Session HIP path: $SESSION_HIP"
if [ -f "$SESSION_HIP" ]; then
    log_step "Session HIP file verified: $SESSION_HIP"
else
    log_step "WARNING: Session HIP file does not exist at: $SESSION_HIP"
fi

# Use the full WebSocket URL (including stage)
export API_ENDPOINT="$WEBSOCKET_URL"
export LOCAL_WS_PORT="7007"

log_step "API Endpoint: $API_ENDPOINT"
log_step "Local WebSocket Port: $LOCAL_WS_PORT"

########################### HOUDINI LICENSING #####################
log_step "Configuring Houdini licensing..."
# Retrieve the secret from AWS Secrets Manager
export SIDEFX_API_SECRET=$(aws secretsmanager get-secret-value --secret-id SideFXOAuthCredentials --region "$AWS_REGION" --query 'SecretString' --output text)
export CLIENT_ID=$(echo "$SIDEFX_API_SECRET" | jq -r .sidefx_client)
export CLIENT_SECRET=$(echo "$SIDEFX_API_SECRET" | jq -r .sidefx_secret)

# Houdini Licensing
log_step "Starting Houdini license server..."
/opt/houdini/bin/hserver -q
/opt/houdini/bin/hserver --clientid "$CLIENT_ID" --clientsecret "$CLIENT_SECRET" --host "https://www.sidefx.com/license/sesinetd"
log_step "Authenticating with SideFX..."
/opt/houdini/houdini/sbin/sesictrl login
# log_step "License information:"
# /opt/houdini/houdini/sbin/sesictrl print-license
# /opt/houdini/houdini/sbin/sesictrl dg
log_step "Houdini licensing configured successfully"

INIT_TIME=$(($(date +%s) - START_TIME))
log_step "Initialization completed in ${INIT_TIME}s (hython warming up in background since boot)"

# ============================================================
# Signal hython that licensing + env setup is complete.
# Write config as JSON — env vars set after fork aren't visible to hython.
# ============================================================
log_step "Signaling hython that environment is ready..."
cat > "$HYTHON_READY_SIGNAL" <<READYEOF
{
  "session_id": "$SESSION_ID",
  "session_hip": "$SESSION_HIP",
  "websocket_url": "$WEBSOCKET_URL",
  "aws_region": "$AWS_REGION",
  "input_bucket": "$INPUT_BUCKET",
  "s3_output_bucket": "$S3_OUTPUT_BUCKET",
  "idle_timeout_seconds": "$IDLE_TIMEOUT_SECONDS",
  "idle_warning_seconds": "$IDLE_WARNING_SECONDS",
  "api_endpoint": "$API_ENDPOINT",
  "local_ws_port": "$LOCAL_WS_PORT",
  "data_root": "$DATA_ROOT",
  "aurora_tooling_root": "$AURORA_TOOLING_ROOT"
}
READYEOF
log_step "Config written to ready signal file"

# Start the WebSocket handler
log_step "=========================================="
log_step "Starting Aurora Session (Two-Process Architecture)"
log_step "=========================================="
log_step "Process 1: WebSocket Handler (Pure Python, no hou)"
log_step "Process 2: Houdini Runner (hython, PID $HOUDINI_RUNNER_PID — already warming up)"
log_step "=========================================="

cd "$AURORA_TOOLING_ROOT"

# Process 1: Start WebSocket handler (bridges API Gateway <-> local runner)
# Use hython's bundled Python to avoid needing conda.
# Set SSL_CERT_FILE so the bundled Python can verify TLS certificates (API Gateway WSS).
export SSL_CERT_FILE="/etc/ssl/certs/ca-certificates.crt"
echo "Starting WebSocket handler..."
/opt/houdini/python/bin/python3.11 "$AURORA_TOOLING_ROOT/runtime/session/websocket_handler.py" &
WS_HANDLER_PID=$!
echo "WebSocket handler PID: $WS_HANDLER_PID"

echo "=========================================="
echo "Both processes running"
echo "=========================================="

# Wait for either process to exit
wait -n $WS_HANDLER_PID $HOUDINI_RUNNER_PID
EXIT_CODE=$?

echo "=========================================="
echo "One process ended with exit code: $EXIT_CODE"
echo "Terminating remaining processes..."

# Kill both processes
kill $WS_HANDLER_PID 2>/dev/null || true
kill $HOUDINI_RUNNER_PID 2>/dev/null || true

# Wait for cleanup
sleep 2