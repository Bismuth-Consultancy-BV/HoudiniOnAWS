import argparse
import hashlib
import logging
import os
import tarfile
import requests
from tqdm import tqdm

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("Houdini Downloader")

def download_file(url: str, path: str):
    """Download a file with a progress bar."""
    logger.info(f"Downloading file to {path}")
    response = requests.get(url, stream=True)
    if response.status_code != 200:
        logger.error(f"Failed to download the file: {response.status_code}")
        raise Exception("Could not download file at URL")

    with open(path, "wb") as f, tqdm(
        total=int(response.headers.get("Content-Length", 0)),
        unit="iB",
        unit_scale=True,
        desc="Downloading",
    ) as progress_bar:
        for chunk in response.iter_content(1024):
            progress_bar.update(len(chunk))
            f.write(chunk)

def verify_file_checksum(path: str, expected_hash: str):
    """Verify the checksum of a downloaded file."""
    logger.info("Verifying file checksum...")
    hasher = hashlib.md5()
    with open(path, "rb") as f, tqdm(
        total=os.path.getsize(path),
        unit="iB",
        unit_scale=True,
        desc="Checksum Matching",
    ) as progress_bar:
        for chunk in iter(lambda: f.read(4096), b""):
            hasher.update(chunk)
            progress_bar.update(len(chunk))

    if hasher.hexdigest() != expected_hash:
        logger.error("Checksum verification failed.")
        raise Exception("Could not verify checksum")
    logger.info("Checksum verified successfully.")

def extract_tar_file(source_path: str, target_dir: str):
    """Extracts a tar.gz file to a specified directory and cleans up the source file."""
    logger.info(f"Extracting {source_path} to {target_dir}")
    with tarfile.open(source_path, "r:gz") as tar:
        tar.extractall(path=target_dir)
        extracted_name = tar.getnames()[0]

    extracted_path = os.path.join(target_dir, extracted_name)
    new_path = os.path.join(target_dir, "build")
    os.rename(extracted_path, new_path)
    os.remove(source_path)
    logger.info(f"Extraction complete. Build directory: {new_path}")

def main():
    parser = argparse.ArgumentParser(description="Download and extract Houdini installer")
    parser.add_argument("--download-url", required=True, help="URL to download the file from")
    parser.add_argument("--filename", required=True, help="Name of the file to download")
    parser.add_argument("--hash", required=True, help="Expected MD5 hash of the file")
    parser.add_argument("--installer-path", default="/houdini_installer/", 
                       help="Path where the installer will be downloaded (default: /houdini_installer/)")
    
    args = parser.parse_args()
    
    # Create installer directory if it doesn't exist
    os.makedirs(args.installer_path, exist_ok=True)
    
    # Download, verify, and extract
    file_path = os.path.join(args.installer_path, args.filename)
    download_file(args.download_url, file_path)
    verify_file_checksum(file_path, args.hash)
    extract_tar_file(file_path, args.installer_path)

if __name__ == "__main__":
    main()