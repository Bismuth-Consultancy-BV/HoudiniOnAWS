#!/bin/bash -u
set -e

export_folder="$AURORA_TOOLING_ROOT/SHARED/OUT/"
zip_file="/tmp/export.zip"
max_retries=2
retry_delay=5

# Get S3 destination from first argument
if [ $# -lt 1 ]; then
    echo "Usage: $0 <s3_output_file>"
    exit 1
fi
S3_OUTPUT_FILE="$1"

# Remove any previous zip
rm -f "$zip_file"

# Zip the contents of export_folder (not the folder itself)
cd "$export_folder"
zip -r "$zip_file" ./*

# Upload the zip to S3 with retries
for ((i=1; i<=max_retries; i++)); do
    if aws s3 cp "$zip_file" "$S3_OUTPUT_FILE" --region "$AWS_REGION" --only-show-errors; then
        echo "Uploaded $zip_file to $S3_OUTPUT_FILE"
        # log_message "Uploaded output zip to S3" # Uncomment if log_message is defined
        break
    else
        echo "Upload failed (attempt $i/$max_retries). Retrying in $retry_delay seconds..."
        sleep $retry_delay
    fi
    if [ $i -eq $max_retries ]; then
        echo "Upload failed after $max_retries attempts."
        exit 1
    fi
done