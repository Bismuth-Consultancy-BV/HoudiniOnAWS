#!/bin/bash -u
set -e  # Exit on error

# Cleanup function on complete or error
terminate_instance() {
    ############################################################################################
    echo "Cleaning up the instance..."
    # Signal termination to the Auto Scaling group
    INSTANCE_ID=$(curl -s http://169.254.169.254/latest/meta-data/instance-id)
    sleep 5 # Let the logs finish uploading to Cloudwatch
    aws ec2 terminate-instances --instance-ids "$INSTANCE_ID" --region "$AWS_REGION"
    ############################################################################################
}
trap terminate_instance EXIT

# Set up the environment
source /opt/miniconda/etc/profile.d/conda.sh
conda activate aurora_env
python --version


echo "--------------------"
echo "AWS REGION: $AWS_REGION"
echo "--------------------"

# Retrieve the instance ID from the metadata service
INSTANCE_ID=$(curl -s http://169.254.169.254/latest/meta-data/instance-id)
# Retrieve all tags associated with this instance in JSON format
TAGS_JSON=$(aws ec2 describe-tags --region "$AWS_REGION" --filters "Name=resource-id,Values=$INSTANCE_ID" --output json)

echo "--------------------"
echo "TAGS_JSON: $TAGS_JSON"
echo "--------------------"

# Convert the array of tags into a single JSON object (MESSAGE_BODY)
# Each tag's Key becomes a property key and its Value the property value.
MESSAGE_BODY=$(echo "$TAGS_JSON" | jq '(.Tags | map({(.Key): .Value}) | add)')
S3_JOB_PACKAGE=$(echo "$MESSAGE_BODY" | jq -r '.jobpackage')
JOB_ID=$(echo "$MESSAGE_BODY" | jq -r '.jobid')


echo "--------------------"
echo "MESSAGE_BODY: $MESSAGE_BODY"
echo "--------------------"


# Clean up previous runs
echo "--------------------"
echo "Cleaning up all old files..."
echo "--------------------"
sudo rm -rf "$AURORA_TOOLING_ROOT"/SHARED


# Download files from S3
echo "--------------------"
echo "Downloading files from S3..."
echo "--------------------"
chmod +x "$AURORA_TOOLING_ROOT"/runtime/s3/download_file.sh
"$AURORA_TOOLING_ROOT"/runtime/s3/download_file.sh "$S3_JOB_PACKAGE"


echo "--------------------"
echo "Logging S3 download."
echo "--------------------"
ls -l "$AURORA_TOOLING_ROOT"/SHARED/*


# Run the main script
echo "--------------------"
echo "Running Houdini job with directive..."
echo "--------------------"
python "$AURORA_TOOLING_ROOT/runtime/run.py" --process_hip --work_directive '$DATA_ROOT/houdini_directive.json'


echo "--------------------"
echo "Logging Houdini result."
echo "--------------------"
ls -l "$AURORA_TOOLING_ROOT"/SHARED/OUT/*


# # Run the main script (Unreal)
# # This is where you would add your Unreal Engine specific code.
# echo "--------------------"
# echo "Running Unreal job with directive..."
# echo "--------------------"
# python "$AURORA_TOOLING_ROOT/runtime/run.py" --process_unreal --work_directive '$DATA_ROOT/unreal_directive.json'


# echo "--------------------"
# echo "Logging Unreal result."
# echo "--------------------"
# ls -l "$AURORA_TOOLING_ROOT"/SHARED/OUT/*


echo "--------------------"
echo "Uploading result to S3."
echo "--------------------"
S3_OUTPUT_FILE="s3://$S3_OUTPUT_BUCKET/$JOB_ID/JobResult.zip"
chmod +x "$AURORA_TOOLING_ROOT"/runtime/s3/upload_file.sh
"$AURORA_TOOLING_ROOT"/runtime/s3/upload_file.sh "$S3_OUTPUT_FILE"
