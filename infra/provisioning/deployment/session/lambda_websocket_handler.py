"""
Lambda handler for WebSocket connections in interactive Houdini sessions.
Routes messages directly between browser and EC2 WebSocket connections.
No SQS - pure WebSocket messaging for low-latency interactions.
"""

import json
import logging
import os
import time
import uuid
import boto3
from botocore.exceptions import ClientError

# Initialize logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Initialize AWS clients
dynamodb = boto3.resource("dynamodb")
ec2 = boto3.client("ec2")
s3 = boto3.client("s3")

# Environment variables
SESSIONS_TABLE = os.environ["SESSIONS_TABLE"]
INPUT_BUCKET = os.environ["INPUT_BUCKET"]


def get_apigw_management_client(event):
    """Create API Gateway Management API client from event context."""
    domain_name = event["requestContext"]["domainName"]
    stage = event["requestContext"]["stage"]

    return boto3.client(
        "apigatewaymanagementapi", endpoint_url=f"https://{domain_name}/{stage}"
    )


def connect_handler(event, context):
    """
    Handle WebSocket connection.

    Two types of connections:
    1. Browser client - creates new session (no HDA at connect time)
    2. EC2 instance - includes 'session_id' query param, joins existing session
    """
    connection_id = event["requestContext"]["connectionId"]
    query_params = event.get("queryStringParameters", {}) or {}

    logger.info(f"WebSocket connection: {connection_id}")
    logger.info(f"Query params: {query_params}")

    table = dynamodb.Table(SESSIONS_TABLE)

    # Check if this is an EC2 connection joining existing session
    if "session_id" in query_params:
        session_id = query_params["session_id"]

        try:
            # Update existing session with EC2 connection ID
            table.update_item(
                Key={"session_id": session_id},
                UpdateExpression="SET ec2_connection_id = :ec2_conn, #status = :status",
                ExpressionAttributeNames={"#status": "status"},
                ExpressionAttributeValues={
                    ":ec2_conn": connection_id,
                    ":status": "ready",
                },
            )

            logger.info(f"EC2 connected to session: {session_id}")

            # Notify browser that EC2 WebSocket handler is connected
            # NOTE: This does NOT mean the Houdini runner is ready yet.
            # The actual "ready" status is sent by the EC2 WebSocket handler
            # once the Houdini runner (hython) connects to it locally.
            response = table.get_item(Key={"session_id": session_id})
            if "Item" in response:
                browser_conn = response["Item"].get("browser_connection_id")
                if browser_conn:
                    try:
                        apigw_mgmt = get_apigw_management_client(event)
                        apigw_mgmt.post_to_connection(
                            ConnectionId=browser_conn,
                            Data=json.dumps(
                                {
                                    "status": "ec2_connected",
                                    "message": "EC2 instance connected, initializing Houdini...",
                                }
                            ).encode("utf-8"),
                        )
                    except:
                        pass

            return {"statusCode": 200, "body": "EC2 connected"}

        except ClientError as e:
            logger.error(f"Error updating session: {e}")
            return {"statusCode": 500, "body": str(e)}

    # Browser connection - create new session
    session_id = str(uuid.uuid4())

    try:
        table.put_item(
            Item={
                "session_id": session_id,
                "browser_connection_id": connection_id,
                "connection_id": connection_id,  # For GSI lookup
                "ec2_connection_id": None,
                "status": "connected",
                "instance_id": None,
                "created_at": int(time.time()),
                "ttl": int(time.time()) + 7200,  # 2 hour TTL
            }
        )

        logger.info(f"Browser session created: {session_id}")

        # Note: Cannot send session_id here due to GoneException during $connect
        # Client must send an initial message to get session_id

        return {"statusCode": 200, "body": "Connected"}

    except ClientError as e:
        logger.error(f"Error creating session: {e}")
        return {"statusCode": 500, "body": json.dumps({"error": str(e)})}


def disconnect_handler(event, context):
    """
    Handle WebSocket disconnection.
    Clean up session and terminate EC2 if needed.
    """
    connection_id = event["requestContext"]["connectionId"]

    logger.info(f"WebSocket disconnection: {connection_id}")

    table = dynamodb.Table(SESSIONS_TABLE)

    try:
        # Find session by connection_id
        response = table.query(
            IndexName="ConnectionIdIndex",
            KeyConditionExpression="connection_id = :conn_id",
            ExpressionAttributeValues={":conn_id": connection_id},
        )

        if response["Items"]:
            session = response["Items"][0]
            session_id = session["session_id"]
            instance_id = session.get("instance_id")
            browser_conn = session.get("browser_connection_id")
            ec2_conn = session.get("ec2_connection_id")

            # If browser disconnects, terminate everything
            if connection_id == browser_conn:
                logger.info(f"Browser disconnected, cleaning up session {session_id}")

                # Notify EC2 to shut down if connected
                if ec2_conn:
                    try:
                        apigw_mgmt = get_apigw_management_client(event)
                        apigw_mgmt.post_to_connection(
                            ConnectionId=ec2_conn,
                            Data=json.dumps({"action": "terminate"}).encode("utf-8"),
                        )
                    except:
                        pass

                # Terminate EC2 instance
                if instance_id:
                    try:
                        ec2.terminate_instances(InstanceIds=[instance_id])
                        logger.info(f"Terminated instance {instance_id}")
                    except ClientError as e:
                        logger.error(f"Error terminating instance: {e}")

                # Delete session
                table.delete_item(Key={"session_id": session_id})

            # If EC2 disconnects, notify browser
            elif connection_id == ec2_conn:
                logger.info(f"EC2 disconnected from session {session_id}")

                if browser_conn:
                    try:
                        apigw_mgmt = get_apigw_management_client(event)
                        apigw_mgmt.post_to_connection(
                            ConnectionId=browser_conn,
                            Data=json.dumps(
                                {
                                    "status": "disconnected",
                                    "message": "Houdini instance disconnected",
                                }
                            ).encode("utf-8"),
                        )
                    except:
                        pass

        return {"statusCode": 200, "body": "Disconnected"}

    except ClientError as e:
        logger.error(f"Error handling disconnect: {e}")
        return {"statusCode": 500, "body": str(e)}


def message_handler(event, context):
    """
    Route messages between browser and EC2 connections.

    Messages from browser → EC2
    Messages from EC2 → browser

    Special actions handled by Lambda:
    - start_session: Launch EC2 instance
    - terminate_session: Clean up and terminate
    """
    connection_id = event["requestContext"]["connectionId"]
    apigw_mgmt = get_apigw_management_client(event)

    try:
        body = json.loads(event["body"])
        action = body.get("action")
        session_id_in_body = body.get("session_id")  # EC2 includes this in all messages

        logger.info(f"Message from {connection_id}: {action}")

        # Get session - try by session_id first (for EC2 messages), then by connection_id
        table = dynamodb.Table(SESSIONS_TABLE)

        if session_id_in_body:
            # Direct query by session_id (EC2 messages include this)
            response = table.get_item(Key={"session_id": session_id_in_body})
            if "Item" not in response:
                send_to_connection(
                    apigw_mgmt, connection_id, {"error": "Session not found"}
                )
                return {"statusCode": 404, "body": "Session not found"}
            session = response["Item"]
        else:
            # Query by connection_id (browser messages)
            response = table.query(
                IndexName="ConnectionIdIndex",
                KeyConditionExpression="connection_id = :conn_id",
                ExpressionAttributeValues={":conn_id": connection_id},
            )

            if not response["Items"]:
                send_to_connection(
                    apigw_mgmt, connection_id, {"error": "Session not found"}
                )
                return {"statusCode": 404, "body": "Session not found"}

            session = response["Items"][0]

        session_id = session["session_id"]
        browser_conn = session.get("browser_connection_id")
        ec2_conn = session.get("ec2_connection_id")

        # Handle Lambda-specific actions
        if action == "get_session_id":
            # Send session_id to browser
            send_to_connection(
                apigw_mgmt,
                connection_id,
                {
                    "message": "Connected",
                    "session_id": session_id,
                },
            )
            return {"statusCode": 200, "body": "Session ID sent"}

        elif action == "request_upload_url":
            # Generate presigned URL for file upload
            return handle_request_upload_url(session, body, apigw_mgmt, connection_id)

        elif action == "start_session":
            return handle_start_session(session, apigw_mgmt, connection_id, body)

        elif action == "terminate_session":
            return handle_terminate_session(session, apigw_mgmt, connection_id)

        # Route messages between connections
        elif connection_id == browser_conn:
            # Browser → EC2
            if ec2_conn:
                send_to_connection(apigw_mgmt, ec2_conn, body)
                logger.info(f"Routed message from browser to EC2")
            else:
                send_to_connection(
                    apigw_mgmt, connection_id, {"error": "EC2 not connected yet"}
                )

        elif connection_id == ec2_conn:
            # EC2 → Browser
            if browser_conn:
                send_to_connection(apigw_mgmt, browser_conn, body)
                logger.info(f"Routed message from EC2 to browser")
            else:
                logger.warning("Browser not connected")

        return {"statusCode": 200, "body": "Message routed"}

    except Exception as e:
        logger.error(f"Error processing message: {e}")
        try:
            send_to_connection(apigw_mgmt, connection_id, {"error": str(e)})
        except:
            pass
        return {"statusCode": 500, "body": str(e)}


def handle_request_upload_url(session, body, apigw_mgmt, connection_id):
    """Generate a presigned URL for uploading HDA file to S3."""
    session_id = session["session_id"]
    filename = body.get("filename")
    content_type = body["content_type"]

    if not INPUT_BUCKET:
        send_to_connection(
            apigw_mgmt, connection_id, {"error": "S3 bucket not configured"}
        )
        return {"statusCode": 500, "body": "S3 bucket not configured"}

    try:
        # Generate S3 key: session_id/filename
        s3_key = f"{session_id}/{filename}"

        # Generate presigned URL for PUT operation (15 minute expiry)
        presigned_url = s3.generate_presigned_url(
            "put_object",
            Params={"Bucket": INPUT_BUCKET, "Key": s3_key, "ContentType": content_type},
            ExpiresIn=900,  # 15 minutes
        )

        logger.info(f"Generated presigned URL for {s3_key}")
        
        # Update session with the S3 key for later use
        table = dynamodb.Table(SESSIONS_TABLE)
        table.update_item(
            Key={"session_id": session_id},
            UpdateExpression="SET s3_key = :key, hda_file = :file",
            ExpressionAttributeValues={
                ":key": s3_key,
                ":file": filename
            },
        )

        send_to_connection(
            apigw_mgmt,
            connection_id,
            {
                "action": "upload_url_ready",
                "session_id": session_id,
                "upload_url": presigned_url,
                "s3_key": s3_key,
                "bucket": INPUT_BUCKET,
            },
        )

        return {"statusCode": 200, "body": "Presigned URL generated"}

    except ClientError as e:
        logger.error(f"Error generating presigned URL: {e}")
        send_to_connection(
            apigw_mgmt,
            connection_id,
            {"error": f"Failed to generate upload URL: {str(e)}"},
        )
        return {"statusCode": 500, "body": str(e)}


def handle_start_session(session, apigw_mgmt, connection_id, body=None):
    """Launch EC2 instance for the session (no HDA — loaded later via menu)."""
    session_id = session["session_id"]
    
    # Get idle timeout from request body (in minutes, convert to seconds)
    body = body or {}
    idle_timeout_minutes = body.get("idle_timeout_minutes", 15)
    idle_timeout_seconds = idle_timeout_minutes * 60
    
    # Get warning leadup time (in minutes, convert to seconds)
    idle_warning_minutes = body.get("idle_warning_minutes", 2)
    idle_warning_seconds = idle_warning_minutes * 60

    if session.get("instance_id"):
        send_to_connection(
            apigw_mgmt,
            connection_id,
            {"error": "Session already started", "instance_id": session["instance_id"]},
        )
        return {"statusCode": 400, "body": "Already started"}

    try:
        launch_template_name = os.environ["LAUNCH_TEMPLATE_NAME"]
        websocket_url = os.environ["WEBSOCKET_API_ENDPOINT"]
        input_bucket = os.environ["INPUT_BUCKET"]
        output_bucket = os.environ["OUTPUT_BUCKET"]

        tags = [
            {"Key": "session_id", "Value": session_id},
            {"Key": "mode", "Value": "interactive"},
            {"Key": "Name", "Value": f"Houdini Interactive - {session_id[:8]}"},
            {"Key": "websocket_url", "Value": websocket_url},
            {"Key": "input_bucket", "Value": input_bucket},
            {"Key": "s3_output_bucket", "Value": output_bucket},
            {"Key": "idle_timeout_seconds", "Value": str(idle_timeout_seconds)},
            {"Key": "idle_warning_seconds", "Value": str(idle_warning_seconds)},
        ]

        instance = ec2.run_instances(
            LaunchTemplate={
                "LaunchTemplateName": launch_template_name,
                "Version": "$Latest",
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

        # Update session with instance ID
        table = dynamodb.Table(SESSIONS_TABLE)
        table.update_item(
            Key={"session_id": session_id},
            UpdateExpression="SET instance_id = :iid, #status = :status",
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={":iid": instance_id, ":status": "starting"},
        )

        logger.info(f"Started instance {instance_id} for session {session_id}")

        send_to_connection(
            apigw_mgmt,
            connection_id,
            {
                "action": "session_started",
                "session_id": session_id,
                "instance_id": instance_id,
                "status": "starting",
                "message": "EC2 instance is starting. This may take 1-2 minutes.",
            },
        )

        return {"statusCode": 200, "body": "Session started"}

    except ClientError as e:
        logger.error(f"Error launching instance: {e}")
        send_to_connection(
            apigw_mgmt, connection_id, {"error": f"Failed to launch instance: {str(e)}"}
        )
        return {"statusCode": 500, "body": str(e)}


def handle_terminate_session(session, apigw_mgmt, connection_id):
    """Terminate the EC2 instance and clean up session."""
    session_id = session["session_id"]
    instance_id = session.get("instance_id")
    ec2_conn = session.get("ec2_connection_id")

    # Notify EC2 to shut down gracefully
    if ec2_conn:
        try:
            send_to_connection(apigw_mgmt, ec2_conn, {"action": "terminate"})
        except:
            pass

    # Terminate instance
    if instance_id:
        try:
            ec2.terminate_instances(InstanceIds=[instance_id])
            logger.info(f"Terminated instance {instance_id}")
        except ClientError as e:
            logger.error(f"Error terminating instance: {e}")

    # Delete session
    table = dynamodb.Table(SESSIONS_TABLE)
    table.delete_item(Key={"session_id": session_id})

    send_to_connection(
        apigw_mgmt,
        connection_id,
        {"action": "session_terminated", "session_id": session_id},
    )

    return {"statusCode": 200, "body": "Session terminated"}


def send_to_connection(apigw_mgmt, connection_id, data):
    """Send data to WebSocket connection."""
    try:
        apigw_mgmt.post_to_connection(
            ConnectionId=connection_id, Data=json.dumps(data).encode("utf-8")
        )
    except ClientError as e:
        if e.response["Error"]["Code"] == "GoneException":
            logger.warning(f"Connection {connection_id} is gone")
        else:
            logger.error(f"Error sending to connection {connection_id}: {e}")
        raise
