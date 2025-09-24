import json
import os
import uuid
import argparse
import boto3
from botocore.exceptions import NoCredentialsError, PartialCredentialsError

AURORA_TOOLING_ROOT = os.getenv("AURORA_TOOLING_ROOT", "")
if not AURORA_TOOLING_ROOT:
    raise ValueError("AURORA_TOOLING_ROOT environment variable is not set.")

def send_message_to_sqs(
    s3_file_uri:str
):
    """
    This function sends a message to the SQS queue with the s3_file_uri, request_id and response_queue_url.
    It will trigger Lambda to start an EC2 to process the file and return the response to the response_queue_url.
    """
    with open(os.path.join(AURORA_TOOLING_ROOT, "samples", "tf_outputs.json"), "r", encoding="utf-8") as f:
        tf_outputs = json.load(f)
        request_queue_url = tf_outputs["request_queue_url"]
        response_queue_url = tf_outputs["response_queue_url"]
        aws_region = tf_outputs["aws_region"]

    # Create SQS client
    session = boto3.session.Session()
    sqs = session.client(service_name="sqs", region_name=aws_region)

    message_request_id = str(uuid.uuid4())
    message = {
        "jobpackage": s3_file_uri,
        "jobid": message_request_id,
        "response_queue_url": response_queue_url,
    }

    try:
        sqs_response = sqs.send_message(
            QueueUrl=request_queue_url, MessageBody=json.dumps(message)
        )
        return sqs_response, message_request_id
    except NoCredentialsError as exc:
        raise ValueError("Error: No AWS credentials found.") from exc
    except PartialCredentialsError as exc:
        raise ValueError("Error: Incomplete AWS credentials.") from exc
    except Exception as exc:
        raise ValueError(f"An error occurred: {exc}") from exc


if __name__ == "__main__":
    argparser = argparse.ArgumentParser(
        description="Send a request to the Aurora SQS queue and poll for the response."
    )
    argparser.add_argument(
        "--s3_file_uri",
        type=str,
        required=True,
        help="The S3 file URL to process.",
    )

    args = argparser.parse_args()

    if not str(args.s3_file_uri).startswith("s3://"):
        raise ValueError("Invalid S3 File URL. It must start with 's3://'.")

    response, scene_id = send_message_to_sqs(
        s3_file_uri=args.s3_file_uri
    )