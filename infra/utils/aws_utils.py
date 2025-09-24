import boto3
import json
import subprocess
import requests
from botocore.exceptions import ClientError


def get_aws_user_id() -> str:
    """Gets the AWS user ID. It will grab whatever is configured using aws configure."""
    result = subprocess.run(
        ["aws", "sts", "get-caller-identity", "--output", "json"],
        check=True,
        capture_output=True,
        text=True,
    )
    outputs = json.loads(result.stdout)
    return outputs["UserId"]


def get_aws_region() -> str:
    """Gets the AWS region. It will grab whatever is configured using aws configure."""
    # For local development, we can use the AWS CLI to get the region.
    # This will work if the user has configured their AWS CLI with 'aws configure'.
    try:
        result = subprocess.run(
            ["aws", "configure", "get", "region"],
            check=True,
            capture_output=True,
            text=True,
        )
        
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except Exception:
        pass
    
    # Fallback to EC2 metadata if available, this is useful for running on EC2 instances.
    try:
        
        response = requests.get('http://169.254.169.254/latest/meta-data/placement/region', timeout=0.5)
        if response.status_code == 200:
            region = response.text.strip()
            print(f"Using region from EC2 metadata: {region}")
            return region
    except Exception:
        pass

    raise RuntimeError("Error using AWS CLI to get region. Did you set the region using 'aws configure'?")

def get_aws_secrets(aws_region: str, secret_name: str) -> dict:
    """Retrieve credentials from AWS Secrets Manager"""
    try:
        session = boto3.session.Session()
        client = session.client(service_name="secretsmanager", region_name=aws_region)

        print(f"Retrieving {secret_name} credentials from AWS Secrets Manager...")
        get_secret_value_response = client.get_secret_value(SecretId=secret_name)
        return json.loads(get_secret_value_response["SecretString"])

    except ClientError as e:
        print(f"Error retrieving credentials from AWS Secrets Manager: {e}")
        raise
    except json.JSONDecodeError as e:
        print(f"Error parsing credentials JSON: {e}")
        raise
