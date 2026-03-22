# Houdini on AWS - Aurora Session Runtime Architecture

```mermaid
graph TB
    subgraph "Client Layer"
        WebApp["Web Browser<br/>(session_tool_demo.html)"]
    end

    subgraph "AWS API Gateway"
        WSGateway["WebSocket API Gateway<br/>wss://xxxxx.execute-api.region.amazonaws.com/production"]
    end

    subgraph "AWS Lambda Functions"
        ConnectLambda["Connection Handler<br/>- Creates session in DynamoDB<br/>- Returns session_id"]
        DisconnectLambda["Disconnection Handler<br/>- Cleans up session<br/>- Terminates EC2"]
        MessageLambda["Message Router<br/>- start_session: Launch EC2<br/>- update_param: Forward to EC2<br/>- terminate_session: Cleanup"]
    end

    subgraph "AWS DynamoDB"
        SessionsTable["Sessions Table<br/>- session_id (PK)<br/>- connection_id (GSI)<br/>- ec2_instance_id<br/>- session_status<br/>- last_activity"]
    end

    subgraph "AWS EC2 - Aurora Session Instance"
        EC2["g4dn.4xlarge GPU Instance"]
        
        subgraph "Two-Process Architecture"
            Entrypoint["entrypoint.sh<br/>- Configures Houdini licensing<br/>- Downloads HDA from S3<br/>- Launches both processes"]
            WSHandler["websocket_handler.py<br/>- Pure Python + asyncio<br/>- Bridges API Gateway ↔ local runner<br/>- WebSocket server on port 7007"]
            HoudiniRunner["houdini_runner.py<br/>- Runs in hython<br/>- Loads session_runner.hip + HDA<br/>- Processes parameters & exports GLTF"]
            HoudiniEngine["Houdini Engine<br/>- HDA parameter manipulation<br/>- Geometry processing<br/>- GLTF export via ROP"]
        end
    end

    subgraph "AWS S3 Storage"
        InputS3["Input Files<br/>- .hip files<br/>- .hda files<br/>- .bgeo.sc files<br/>- Textures/assets"]
        OutputS3["Output Files<br/>- Processed geometry<br/>- Exported results<br/>- Session logs"]
    end

    %% Client to API Gateway
    WebApp -->|"1. WebSocket Connect"| WSGateway
    
    %% API Gateway to Lambda routes
    WSGateway -->|"$connect"| ConnectLambda
    WSGateway -->|"$disconnect"| DisconnectLambda
    WSGateway -->|"$default"| MessageLambda
    
    %% Lambda to DynamoDB
    ConnectLambda -->|"Create session"| SessionsTable
    DisconnectLambda -->|"Delete session"| SessionsTable
    MessageLambda -->|"Query/Update"| SessionsTable
    
    %% Lambda to EC2
    MessageLambda -->|"2. Launch EC2<br/>(start_session)"| EC2
    MessageLambda -->|"3. Terminate EC2<br/>(terminate_session)"| EC2
    
    %% EC2 internal flow
    EC2 --> Entrypoint
    Entrypoint --> WSHandler
    Entrypoint --> HoudiniRunner
    HoudiniRunner --> HoudiniEngine
    
    %% Bidirectional WebSocket communication
    WSHandler <-->|"4. Bidirectional Messages<br/>- geometry updates<br/>- parameter changes<br/>- status updates"| MessageLambda
    
    %% EC2 to S3
    HoudiniEngine -->|"Read"| InputS3
    HoudiniEngine -->|"Write"| OutputS3
    
    %% Browser receives updates
    MessageLambda -->|"5. Forward geometry<br/>to client"| WSGateway
    WSGateway -->|"Render in browser"| WebApp

    style WebApp fill:#4a90e2,color:#fff
    style WSGateway fill:#e74c3c,color:#fff
    style ConnectLambda fill:#f39c12,color:#000
    style DisconnectLambda fill:#f39c12,color:#000
    style MessageLambda fill:#f39c12,color:#000
    style EC2 fill:#27ae60,color:#fff
    style HoudiniEngine fill:#e67e22,color:#fff
    style SessionsTable fill:#f1c40f,color:#000
    style InputS3 fill:#9b59b6,color:#fff
    style OutputS3 fill:#9b59b6,color:#fff
    style Entrypoint fill:#16a085,color:#fff
    style WSHandler fill:#16a085,color:#fff
    style HoudiniRunner fill:#16a085,color:#fff
```

## Runtime Flow

1. **WebSocket Connect** - Browser establishes WebSocket connection to API Gateway
2. **Launch EC2 - Lambda launches Aurora Session EC2 instance on start_session message EC2 instance on start_session message
3. **Terminate EC2** - EC2 terminates on disconnect or terminate_session message
4. **Bidirectional Messages** - Real-time communication with ~50ms latency for parameter updates and geometry streaming
5. **Forward Geometry** - Processed geometry flows back to browser for rendering

## Key Components

- **WebSocket API Gateway** - Single communication endpoint for real-time bidirectional messaging
- **Three Lambda Functions** - Handle connection lifecycle and message routing
- **DynamoDB Sessions Table** - Tracks active sessions with connection and EC2 instance mappings
- **EC2 with Houdini** - Two-process architecture: WebSocket handler + hython runner
- **S3 Storage** - Input/output file storage for HDA assets and GLTF results
