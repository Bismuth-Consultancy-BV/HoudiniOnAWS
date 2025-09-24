import logging
import os
import boto3
from botocore.exceptions import ClientError
import json

# Initialize logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Initialize AWS clients
ec2 = boto3.client("ec2")
sqs = boto3.client("sqs")


def lambda_handler(event, context):
    try:
        # Retrieve environment variables
        launch_template_name = os.environ["LAUNCH_TEMPLATE_NAME"]
        launch_template_version = os.environ.get("LAUNCH_TEMPLATE_VERSION", "$Latest")
        subnet_id = os.environ["SUBNET_ID"]
        security_group_id = os.environ["SECURITY_GROUP_ID"]

        print(f"Launch Template Name: {launch_template_name}")
        print(f"Launch Template Version: {launch_template_version}")
        print(f"Subnet ID: {subnet_id}")
        print(f"Security Group ID: {security_group_id}")

        # Extract messages from the event
        messages = event.get("Records", [])
        num_messages = len(messages)

        if num_messages > 0:
            logger.info(
                "Messages in event: %d. Starting an EC2 instance.", num_messages
            )

            # Extract the first message body
            message_body = json.loads(messages[0]["body"])
            logger.info("Message Body: %s", message_body)

            # Convert the JSON message into tags
            tags = [
                {"Key": key, "Value": str(value)} for key, value in message_body.items()
            ]
            tags.append({"Key": "Name", "Value": "Aurora Processing Instance"})
            logger.info("Tags to be added: %s", tags)

            # Start an EC2 instance using the launch template and network interface
            instance = ec2.run_instances(
                LaunchTemplate={
                    "LaunchTemplateName": launch_template_name,
                    "Version": launch_template_version,
                },
                MinCount=1,
                MaxCount=1,
                TagSpecifications=[
                    {
                        "ResourceType": "instance",
                        "Tags": tags,
                    }
                ],
            )

            instance_id = instance["Instances"][0]["InstanceId"]
            logger.info("Started EC2 instance with ID: %s", instance_id)

            return {
                "statusCode": 200,
                "body": f"Started EC2 instance with ID: {instance_id}",
            }
        logger.info("No messages in the event. No action taken.")
        return {"statusCode": 200, "body": "No messages in the event."}

    except KeyError as e:
        logger.error("Missing environment variable: %s", e)
        return {"statusCode": 500, "body": f"Missing environment variable: {e}"}

    except ClientError as e:
        logger.error("Error interacting with AWS services: %s", e)
        return {"statusCode": 500, "body": f"Error interacting with AWS services: {e}"}

    except Exception as e:
        logger.error("Unexpected error: %s", e)
        return {"statusCode": 500, "body": f"Unexpected error: {e}"}
