#!/bin/bash -u

set -e

# Predefined download location and extraction folder
download_location=$(mktemp -u /tmp/aurora_job_files.XXXXXX.zip)
extract_folder="$AURORA_TOOLING_ROOT/SHARED/"
max_retries=2
retry_delay=10

# Function to download a file from S3 using AWS CLI with retries
download_file() {
    local url="$1"
    local destination="$2"
    local attempt=0
    
    if [ -f "$destination" ]; then
        echo "File already exists: $destination"
    else
        echo "Downloading $url to $destination"
        
        while [ $attempt -lt $max_retries ]; do
            if command -v aws &> /dev/null; then
                if aws s3 cp "$url" "$destination" --only-show-errors --no-progress; then
                    echo "Download complete: $destination"
                    return
                fi
            else
                echo "Error: AWS CLI is not installed."
                exit 1
            fi
            
            echo "Download failed, retrying in $retry_delay seconds..."
            sleep $retry_delay
            attempt=$((attempt + 1))
        done
        
        echo "Failed to download file after $max_retries attempts."
        exit 1
    fi
}

# Function to extract the downloaded file with error handling
extract_file() {
    local file="$1"
    local destination="$2"
    
    if [ -d "$destination" ]; then
        echo "Extraction folder already exists: $destination"
    else
        echo "Creating extraction folder: $destination"
        mkdir -p "$destination"
        echo "Extracting $file to $destination"
        if unzip "$file" -d "$destination" > /dev/null; then
            echo "Extraction complete."
        else
            echo "Extraction failed. Deleting incomplete extraction folder."
            rm -rf "$destination"
            exit 1
        fi
    fi
}

# Main script execution
if [ -z "$1" ]; then
    echo "Usage: $0 <s3_url>"
    exit 1
fi

file_url="$1"

echo "Downloading and extracting jobpackage from $file_url using AWS CLI"
download_file "$file_url" "$download_location"
extract_file "$download_location" "$extract_folder"
sudo rm -rf "$download_location"