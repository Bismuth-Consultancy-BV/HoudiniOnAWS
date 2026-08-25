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
# Instance metadata first: licensing below needs AWS_REGION.
# ============================================================
log_step "Retrieving instance metadata..."
INSTANCE_ID=$(curl -s http://169.254.169.254/latest/meta-data/instance-id)
AWS_REGION=$(curl -s http://169.254.169.254/latest/meta-data/placement/region)

log_step "Instance ID: $INSTANCE_ID"
log_step "AWS Region: $AWS_REGION"

########################### HOUDINI LICENSING #####################
log_step "Configuring Houdini licensing..."
# Retrieve the secret from AWS Secrets Manager
export SIDEFX_API_SECRET=$(aws secretsmanager get-secret-value --secret-id SideFXOAuthCredentials --region "$AWS_REGION" --query 'SecretString' --output text)
export CLIENT_ID=$(echo "$SIDEFX_API_SECRET" | jq -r .sidefx_client)
export CLIENT_SECRET=$(echo "$SIDEFX_API_SECRET" | jq -r .sidefx_secret)

# Houdini Licensing
#
# Houdini 22 takes API credentials from hserver.ini, where the key line names
# the license server alongside the credentials:
#
#   APIKey=<servername> <client id> <client secret>
#
# Under 21 it was enough to pass --clientid/--clientsecret with a separate
# --host and then run `sesictrl login`. Under 22 that leaves hserver
# unauthenticated - it exits silently, `sesictrl login` finds no session
# ("You are not logged into the license server. [Error L01]") and falls back
# to prompting for an email address, which never terminates on a headless
# instance. So configure the ini and do not call `sesictrl login` at all.
# Pass the API keys to hserver on the command line and write no hserver.ini.
#
# An APIKey line in hserver.ini needs a servername, and the sesinetd URL that
# used to be passed as --host is not the right value for it: with a readable
# ini in place hserver takes the credentials but finds no licenses, and hython
# exits with "No licenses could be found". The credentials work when supplied
# as arguments, so supply them that way and leave the options file alone.
log_step "Starting Houdini license server..."
/opt/houdini/bin/hserver -q
/opt/houdini/bin/hserver --clientid "$CLIENT_ID" --clientsecret "$CLIENT_SECRET"
log_step "Houdini licensing configured (hserver started with API keys)"

# Licensing diagnostics, off by default. Set AURORA_LICENSE_DEBUG=1 in
# /etc/environment (or in the launch template's user_data, which runs before
# this script) to print hserver's status and the account's license inventory -
# useful when a session cannot check out a license. Instance tags are not read
# until later in this script, so they cannot gate this. Both calls are read-only and take their credentials as arguments
# so neither can fall back to the interactive prompt; stdin is closed and
# output capped so a command that prompts anyway cannot flood the log.
if [ "${AURORA_LICENSE_DEBUG:-1}" = "1" ]; then
    log_step "--- hserver status ---"
    timeout 30 /opt/houdini/bin/hserver -l < /dev/null 2>&1 | head -c 4000 || true
    log_step "--- available licenses ---"
    timeout 30 /opt/houdini/houdini/sbin/sesictrl print-license \
        --client-id "$CLIENT_ID" --client-secret "$CLIENT_SECRET" \
        < /dev/null 2>&1 | head -c 4000 || true
    log_step "--- end license diagnostics ---"
fi

# ============================================================
# Launch hython only AFTER licensing is configured. houdini_runner.py
# does `import hou` at module scope, which acquires a license at
# interpreter startup - long before it polls the ready-signal file.
# Starting it earlier races `hserver -q` above and dies with
# "No licenses could be found to run this application" (exit 3).
# Its ~90s cold boot still overlaps the remaining init below.
# ============================================================
export HYTHON_READY_SIGNAL="/tmp/houdini_boot_ready"
# Tee hython's output so we can tell the browser *why* it died if it never
# connects. websocket_handler.py watches AURORA_STARTUP_ERROR_FILE.
export HYTHON_LOG="/tmp/hython_startup.log"
export AURORA_STARTUP_ERROR_FILE="/tmp/aurora_startup_error"
rm -f "$HYTHON_READY_SIGNAL" "$HYTHON_LOG" "$AURORA_STARTUP_ERROR_FILE"

log_step "Starting hython cold boot (license is ready; runs in parallel with remaining init)..."
cd "${AURORA_TOOLING_ROOT:-/opt/aurora}"
/opt/houdini/bin/hython "$AURORA_TOOLING_ROOT/runtime/session/houdini_runner.py" > >(tee -a "$HYTHON_LOG") 2>&1 &
HOUDINI_RUNNER_PID=$!
log_step "hython launched (PID: $HOUDINI_RUNNER_PID) — warming up while we finish init"

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
# Houdini's bundled Python version changes between releases, so resolve the
# interpreter rather than hard-coding a minor version.
HOUDINI_PYTHON=$(ls /opt/houdini/python/bin/python3.* 2>/dev/null \
  | grep -E '/python3\.[0-9]+$' | sort -V | tail -n1)
if [ -z "$HOUDINI_PYTHON" ]; then
    echo "ERROR: no python3.x interpreter under /opt/houdini/python/bin"
    exit 1
fi
"$HOUDINI_PYTHON" "$AURORA_TOOLING_ROOT/runtime/session/websocket_handler.py" &
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

# If hython is the one that died, work out why and hand the reason to the
# WebSocket handler so the browser gets a warning instead of hanging forever.
# The handler polls AURORA_STARTUP_ERROR_FILE, so give it a moment to send
# before we tear the instance down.
if ! kill -0 "$HOUDINI_RUNNER_PID" 2>/dev/null; then
    STARTUP_REASON="Houdini failed to start on the session instance (exit code $EXIT_CODE). See CloudWatch /aws/ec2/aurora-session for the full log."
    if grep -qi "No licenses could be found" "$HYTHON_LOG" 2>/dev/null; then
        STARTUP_REASON="No Houdini license available. The instance signed in to SideFX successfully, but no Houdini Engine license could be checked out. Check your available licenses at sidefx.com."
    fi
    log_step "Runner failed before becoming ready: $STARTUP_REASON"
    echo "$STARTUP_REASON" > "$AURORA_STARTUP_ERROR_FILE"
    log_step "Giving the WebSocket handler a moment to warn the browser..."
    sleep 8
fi

echo "Terminating remaining processes..."

# Kill both processes
kill $WS_HANDLER_PID 2>/dev/null || true
kill $HOUDINI_RUNNER_PID 2>/dev/null || true

# Wait for cleanup
sleep 2