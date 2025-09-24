#!/bin/bash -u
set -e  # Exit on error

# Default values
WORK_DIRECTIVE=""
SIDEFX_CLIENT_ID=""
SIDEFX_CLIENT_SECRET=""

# Parse arguments
while [[ $# -gt 0 ]]; do
    key="$1"
    case $key in
        --work_directive)
            WORK_DIRECTIVE="$2"
            shift
            shift
        ;;
        *)
            echo "Unknown argument: $1"
            shift
        ;;
    esac
done


cleanup_houdini() {
    # Ensure we release the Houdini license no matter what
    /opt/houdini/bin/hserver --blocking-quit
    echo "[HOUDINI] License Released"
    echo "--------------------"
    echo ""
}

# Cleanup function on complete or error
cleanup() {
    
    if [ $COMPLETED_SUCCESSFULLY -eq 1 ]; then
        echo "[HOUDINI] Job completed successfully."
        echo "--------------------"
        echo ""
    else
        echo "[HOUDINI] Job did not complete successfully."
        echo "--------------------"
        echo ""
    fi
    
    # Release the Houdini license
    cleanup_houdini
}
trap cleanup EXIT

COMPLETED_SUCCESSFULLY=0

SIDEFX_CLIENT_ID=$(jq -r '.sidefx_client' $CREDENTIALS_ROOT/houdini_credentials.json)
SIDEFX_CLIENT_SECRET=$(jq -r '.sidefx_secret' $CREDENTIALS_ROOT/houdini_credentials.json)

# Houdini Licensing
/opt/houdini/bin/hserver --clientid "$SIDEFX_CLIENT_ID" --clientsecret "$SIDEFX_CLIENT_SECRET" --host "https://www.sidefx.com/license/sesinetd"
/opt/houdini/houdini/sbin/sesictrl login
/opt/houdini/houdini/sbin/sesictrl print-license
/opt/houdini/houdini/sbin/sesictrl dg

# Run the Houdini processing script
echo "[HOUDINI] Starting Houdini processing script..."
echo "--------------------"
/opt/houdini/bin/hython "$AURORA_TOOLING_ROOT/runtime/houdini/processing.py" --work_directive "$WORK_DIRECTIVE"

# Set the completion flag
COMPLETED_SUCCESSFULLY=1