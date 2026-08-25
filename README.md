# Project Aurora - Containerized Houdini on AWS
Project Aurora allows you to easily set up a containerized Houdini environment on AWS. It supports two modes: **Batch Processing** for offline job processing, and **Session Mode** for real-time interactive Houdini sessions through a web browser. This project is a collaboration between [SideFX Software](https://www.sidefx.com/), [AWS](https://aws.amazon.com/) and [Bismuth Consultancy](http://bismuth.at/). It is provided as is, without any warranty or liability from the parties aforementioned.

This repo is meant to be used as a sample by Pipeline TDs to learn about the possibilities of cloud compute for Houdini. The README explains how to use all the different functionality provided, but is by no means meant to be a tutorial educating you about Houdini/AWS/Docker.

![InfrastructureDiagram](InfrastructureDiagram.jpg)

## Getting Started

> [!WARNING]
> This sample will provide the tooling to provision and run AWS infrastructure to get started with Cloud Compute with Houdini. Running any kind of compute / sample provided below will incur costs on your AWS account! <b>Make sure you understand what you are running before running it!</b>

### Prerequisites
- You have a valid AWS Account with the required permissions.
    - In this sample we are using `eu-north-1` as the AWS region in links and default arguments. <i>You can use any other region you want, but be sure to change the relevant settings accordingly.</i>
    - Ensure you also have sufficient `On-Demand G and VT instances` service quota. They can be requested [here](https://eu-north-1.console.aws.amazon.com/servicequotas/home/services/ec2/quotas/L-DB2E81BA). For the `g4dn.2xlarge` we are using in this sample you need at least a capacity of 8 vCPU. You can also modify this sample to not use any GPU capable instances, but since this sample at some point will also include use of Unreal Engine it has been decided to support GPU instances from the start.
- You have a valid SideFX Account with the required licenses.
    - At least 1 Houdini Engine license is required. (The Unreal/Unity specific licenses do not work!)

### First time setup
1. Clone this repository to disk.
2. Define the `AURORA_TOOLING_ROOT` environment variable on your machine. It should be set to the root folder this cloned repository.
3. Set up a local python environment compatible with this repo's codebase.<br>
    <i>The easiest is to use [Miniconda](https://www.anaconda.com/docs/getting-started/miniconda/install) to set it up automatically using the environment.yml provided in the repo:</i>
    ```bash
    # To create the env:
    conda env create -f environment.yml

    # To remove the env:
    conda remove -n aurora_env --all
    ```
4. Install [Docker](https://www.docker.com/get-started/).
    - Docker is used to locally build a containerized deployment of Houdini.
5. Install [Terraform](https://developer.hashicorp.com/terraform/install).
    - Terraform is used to automatically provision the AWS Infrastructure.
6. Install [Packer](https://developer.hashicorp.com/packer/tutorials/aws-get-started/get-started-install-cli).
    - Packer is used to automatically build the AMI (Amazon Machine Image).
7. Install [AWS CLI](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html).
    - AWS CLI is used to communicate with AWS through commandline. It also securely manages your AWS credentials.
8. Set up your local AWS CLI credentials using `aws configure`.
    - For "default region name" we use `eu-north-1` in this sample.
    - [Information on credentials](https://docs.aws.amazon.com/IAM/latest/UserGuide/security-creds-programmatic-access.html).
9. Create [AWS Secrets](https://aws.amazon.com/secrets-manager/) in the same region as what you used in the `aws configure` command for SideFX API Access Tokens, and Github Credentials:
    - `GithubCredentials` containing `PAT` entry with a [Github PAT](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens) for your Github repo containing your tooling.
    - `SideFXOAuthCredentials` containing `sidefx_client`, `sidefx_secret` [SideFX API Credentials](https://www.sidefx.com/docs/api/credentials/index.html).
10. Create a [EC2 Key Pair](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/create-key-pairs.html) and securily store it locally. 
    - It should be `.pem` format. In this project it is named `aurora-key-pair.pem`.
    - Ensure the key has [the right permissions](https://repost.aws/questions/QUsZqMJGVtQmemkEMVwxUiIw/why-should-i-change-the-permissions-on-the-ssh-pem-file) on your PC, or it will result in an error. 


## Practical Examples
> [!WARNING]
> In the examples below the used filepaths are just examples. Please adjust them where necessary!

### Testing & Developing Locally
To test the docker setup locally; For example to debug or develop easily you can run the main commands in the already setup conda environment.

> [!IMPORTANT]
> All commands shown below are run from within the `$AURORA_TOOLING_ROOT` root folder, and assume you are in the conda environment configured earlier.
> You can enter it as follows after setting it up:
> ```bash
> conda activate aurora_env
> ```

> [!WARNING]
> You will want to modify the `admin_ip_access` variable in [shared_infra.tf](infra/provisioning/deployment/shared_infra.tf) from `0.0.0.0/0` to your own IP. This improves security and allows you to SSH into the EC2 instances.


#### 1. Building Docker Image
<details>
<summary>Instructions</summary>

To build the docker image, follow the below steps.

1. Enter conda env created earlier.
2. Run the the following command from the repository root, which will build the docker image for local testing:
```bash
python infra/build_util.py --build_images
```
</details>

> [!TIP]
>  After the above command completes successfully, you can confirm it exists by running `docker images`. It should list an image named `houdini_aws`.

#### 2. Running Docker Image Locally
<details>
<summary>Instructions</summary>

To test the provided sample (or your own data!) you can do the following:
1. Extract the `$AURORA_TOOLING_ROOT/samples/JobPackageSample.zip` into `$AURORA_TOOLING_ROOT/SHARED/`. 
2. Enter conda env created earlier.
3. Run the already built image with the following command:
```bash
python runtime/batch/run.py --process_hip --work_directive '$DATA_ROOT/houdini_directive.json'
```
</details>

### Defining a JobPackage
In order for Houdini and the Aurora sample to know what and how to process a `.hip` file, you need to create what is called a work directive (`houdini_directive.json`) as part of a job package. A sample of such a work directive (and job package) can be found inside `$AURORA_TOOLING_ROOT/samples/JobPackageSample.zip`. In this section we will look at how you can create your own.

> [!IMPORTANT]
> All filepaths used in the `houdini_directive.json` should alway be relative paths. Paths used for files within the JobPackage zip should always use `$DATA_ROOT` to specify the root of the package.

<details>
<summary>Instructions</summary>

In order to have a JobPackage.zip be valid, you need at least 2 things:
1. `houdini_directive.json`
    - This will contain all the "instructions" that Houdini needs to correctly process your file.
2. Houdini file (`.hip`)
    - This is the houdini file used for processing. You are able to include multiple `.hip` files.

#### Understanding the houdini_directive.json
The work directive is a simple JSON, which contains a list with processing entries. As you can see, the JSON root element is a list (seen by the `[ ]`). This list contains dictionary entries which have all the relevant configs used by the [houdini processor](runtime/batch/processing.py). The processor will process the entries based on the index they have in this list; This can be used to for example cook certain hip files before others. The most important elements are as follows:
- `enabled` - Boolean indicating if this entry should be considered for processing.
- `hip_file` - This is the filepath to the `.hip` that should be processed. `$DATA_ROOT` should always be used to indicate the root of the `.zip` file.
- `hip_file_debug` - (optional) This is an optional filepath you can specify (also relative to `$DATA_ROOT`), where a copy of the input `.hip` will be saved with all of its parameters set based on the `houdini_directive.json`. This is primarily useful for debugging. This field is <i>optional</i>.
- `inputs` - This is a list (also indicated by `[ ]`), containing all of the parameters that need to be set in the `.hip` file. 
    - `node` - The full absolute path to the node that needs to be configured.
    - `parm` - The parameter name that needs to be configured on the relevant node.
    - `value` - The value that needs to be set on the relevant parameter.
    - `type` - The type of value that is being set. This is mainly to detect invalid configurations and help debug potential issues.
        - `literal` - A literal value you want to set.
        - `input_file` - Specified that the value which is set is an input file. This will run a check to make sure the specified input file is actually present / available.
        - `output_file` - Specifies that the value which is set is a file the cook will write. No check is run on it.
    - `required` (optional) - Field indicating whether or not this input file is required for the cook to succeed. Only meaningful alongside `type: input_file`, and defaults to `false` when left out. This is useful when you wish to reuse the same `houdini_directive.json` with multiple input datasets.
- `execute` - This is a list (also indicated by `[ ]`), pointing to buttons that should be pressed to run the cook of the `.hip` file. For example the "Save to Disk" button on a Geometry ROP. You can add as many as you want. Notice that the path is pointing to the actual button parameter itself. (`<node_path>/<parameter_name>`)
```json
[
  {
    "enabled": true,
    "hip_file": "$DATA_ROOT/SampleFile.hip",
    "hip_file_debug": "$DATA_ROOT/OUT/debug/SampleFile.hip",
    "inputs": [
      {
        "node": "/obj/geo1/color1",
        "parm": "colorr",
        "value": 1.0,
        "type": "literal"
      },
      {
        "node": "/obj/geo1/IN_FILE",
        "parm": "file",
        "value": "$DATA_ROOT/IN/rubber_toy.bgeo.sc",
        "type": "input_file",
        "required": true
      },
      {
        "node": "/obj/geo1/EXPORTER",
        "parm": "sopoutput",
        "value": "$DATA_ROOT/OUT/exported_geometry.bgeo.sc",
        "type": "output_file"
      }
    ],
    "execute": [
      "/obj/geo1/EXPORTER/execute"
    ]
  }
]
```

#### Understanding the .hip
It is recommended to either embed all (non-standard) asset definitions into the `.hip`, or ensure you also add the relevant `.hda` files in either the JobPackage or in the tooling of the cloned repo during processing. For the latter two approaches you may need to extend the functionality of this sample to automatically load the relevant asset definitions.


</details>

### Batch Processing on AWS

#### 1. Building the Batch AMI (Amazon Machine Image)
<details>
<summary>Instructions</summary>

To build the batch AMI, run the following from the repository root:
```bash
python infra/build_util.py --build_ami --keypair $AURORA_TOOLING_ROOT/infra/provisioning/aurora-key-pair.pem
```
This will start building an AMI which can later be used to run the containerized Houdini on AWS!
</details>

#### 2. Provisioning Batch Infrastructure
<details>
<summary>Instructions</summary>

To provision the batch infrastructure, you can run the following command:
```bash
python infra/build_util.py --provision_batch_aws
```
This provisions the AWS infrastructure (VPC, SQS/SNS, Lambda, ECS/EC2 wiring) used for batch processing with Houdini.
</details>

#### 3. Uploading Job Package to S3 Input Bucket
Before you can process a Job Package, you need to upload it to the S3 Aurora Input Bucket.

<details>
<summary>Instructions</summary>

To upload a file to the S3 Aurora Input bucket, you can either do this manually through the AWS Console, or with the AWS CLI.
In this sample we will be using the JobPackage found in `$AURORA_TOOLING_ROOT/samples/JobPackageSample.zip`.


To upload the sample file to S3 input bucket, use the following command:
```bash
aws s3 cp samples/JobPackageSample.zip s3://aurora-input-bucket/JobPackageSample.zip
```

If instead you want to upload it manually, visit the [S3 general purpose buckets](https://eu-north-1.console.aws.amazon.com/s3/buckets?region=eu-north-1&bucketType=general) in your AWS account. Be sure to upload it in the `aurora-input-bucket`.
</details>


#### 4. Submitting a Jobpackage for Cloud Compute
Now that everything is configured and set up, you can submit a job-package to the queue for processing on AWS.

<details>
<summary>Instructions</summary>

In this example we will be using the `JobPackageSample` uploaded in step 3. We need to get the `S3 URI`, which looks as follows:
`s3://aurora-input-bucket/test/processing_sample.zip`. That job package contains everything needed to run the process, including the work directive. It is therefore important that you do step 3 for every set of files you wish to process on AWS. Automating that is fairly trivial using the CLI tools AWS provides.


To send a command to the Aurora processing queue, you can use the following command. Notice how we pass the URI to the `s3_file_uri` argument:
```bash
python samples/send_aurora_request.py --s3_file_uri s3://aurora-input-bucket/test/processing_sample.zip
```

Once the job has been kicked off, the EC2 instance will start up and processing will shortly commence. Once the process is running you are able to see what is happening in [Cloudwatch](https://eu-north-1.console.aws.amazon.com/cloudwatch/home?region=eu-north-1#logsV2:log-groups/log-group/$252Faws$252Fec2$252Faurora-jobs), which will log everything. The log group where you can find the logs is `/aws/ec2/aurora-jobs`.

</details>

#### Extending with Unreal Engine
This sample is currently limited to the use of Houdini on AWS, but can easily be extended to also use Unreal Engine. For example to ingest Houdini generated content through Houdini Engine, and use such content to for example automatically generate a map.

To do so, several things will need to be done:
1. Uncomment/extend the lines in [infra/provisioning/building/provision_batch_ami.pkr.hcl](infra/provisioning/building/provision_batch_ami.pkr.hcl) that are responsible for authenticating with GHCR and pulling the image with a pre-built UnrealEngine binary. For more information see the [documentation](https://dev.epicgames.com/documentation/en-us/unreal-engine/quick-start-guide-for-using-container-images-in-unreal-engine) Epic provides.
    - When doing this, make sure you also change the allocated maximum disk space on the AMI, since UE uses quite a lot more disk space then Houdini.
    - You will also need to add a new set of credentials to the Secrets Manager specific to Unreal, just like the ones described above.
2. Uncomment/extend the lines in [runtime/batch/entrypoint.sh](runtime/batch/entrypoint.sh) that are responsible for calling upon your scripts that call Unreal inside the container.
    - The aforementioned scripts that call Unreal and run some arbitrary script inside UE are not part of this sample.

### Session Mode on AWS
Session mode allows users to manipulate Houdini Digital Assets (HDAs) in real-time through a web browser. Instead of submitting a job to a queue, a persistent EC2 instance is launched with a WebSocket connection for bidirectional communication. Parameter changes are sent to Houdini, geometry is exported as GLTF, and the result is displayed in a 3D viewer in the browser.

#### 1. Building the Session AMI and Infrastructure
<details>
<summary>Instructions</summary>

To build the session AMI, run the following from the repository root:
```bash
python infra/build_util.py --build_ami --provision_service_aws --keypair $AURORA_TOOLING_ROOT/infra/provisioning/aurora-key-pair.pem
```
This builds an AMI with Houdini, Vulkan drivers, and the session runtime pre-installed. It is separate from the batch AMI.

Note that `--provision_service_aws` does double duty here: it tells `--build_ami` to build the *session* AMI rather than the batch one, and it provisions the session infrastructure once that AMI exists. A single run of the command above therefore leaves you with both.
</details>

#### 2. Re-provisioning Session Infrastructure
<details>
<summary>Instructions</summary>

The command in step 1 already provisioned the infrastructure. To re-provision it later without rebuilding the AMI — after changing the Terraform or the Lambda handlers, for example — run:
```bash
python infra/build_util.py --provision_service_aws
```
This provisions the session-specific AWS resources: WebSocket API Gateway, Lambda functions for connection management, DynamoDB session table, and a dedicated EC2 launch template.

After provisioning, relevant outputs (including the `websocket_url`) are saved to `samples/tf_outputs.json`.
</details>

#### 3. Configuring the Web Client
<details>
<summary>Instructions</summary>

Edit `webapp/config.js` with the WebSocket endpoint from the provisioning output:
```javascript
export const CONFIG = {
    websocket_url: "wss://your-api-id.execute-api.eu-north-1.amazonaws.com/production",
    idle_timeout_minutes: 15,
    idle_warning_minutes: 2,
    region: "eu-north-1",
    environment: "prod"
};
```
The `websocket_url` can be found in `samples/tf_outputs.json` after provisioning.
</details>

#### 4. Using the Web Interface
<details>
<summary>Instructions</summary>

Serve the webapp locally and open it in a browser:
```bash
cd webapp
python -m http.server 8000
# Visit http://localhost:8000/
```

The workflow is:
1. Click "Start Session" — an EC2 instance is launched (takes 1-2 minutes). The menu bar reports progress; no asset is involved yet.
2. Use `Session > Load HDA` to pick a digital asset. `.hda`, `.hdalc`, `.hdanc`, `.otl`, `.otllc` and `.otlnc` are all accepted. The file is uploaded to S3 and installed into the running session.
3. Parameters from the HDA are automatically displayed as interactive controls, and the asset's first output is cooked and shown in the viewer.
4. Adjust parameters — geometry updates in the 3D viewer. By default every change cooks immediately; see [Controlling when the session cooks](#controlling-when-the-session-cooks) to change that.
5. Use `Scene > Export` to save the result as a `.glb` file. See [Understanding HDA outputs](#understanding-hda-outputs) if your asset has more than one output.
6. Use `Session > Terminate` when done to clean up the EC2 instance.

You can load a different asset at any time with `Session > Load HDA` — the session stays up and swaps the asset, which is much quicker than starting over.

> [!WARNING]
> The instance is terminated when the browser disconnects (closing the tab, or losing the connection), or when you terminate the session from the menu. **There is no idle timeout yet** — `idle_timeout_minutes` in `webapp/config.js` is passed through to the instance but nothing acts on it, so a session left open in a browser tab keeps running, and keeps costing money.
</details>

#### Controlling when the session cooks
<details>
<summary>Instructions</summary>

Every parameter change normally triggers a cook on the instance and a fresh GLB in the viewer. On a heavy asset that is more round trips than you want, so the header of the **Houdini Console** panel (bottom-left of the viewer) carries two controls:

- **Cook** — untick to stop cooking altogether. Parameter changes are still sent to the session and applied, so nothing is lost; the viewer simply stops updating. Ticking it again cooks once and brings the viewer back up to date. Useful for setting up several parameters before paying for a single cook.
- **Auto / On Mouse Up** — `Auto` (the default) cooks continuously while you drag a slider. `On Mouse Up` waits until you release it, which is the better choice when a single cook takes more than a moment.

While a cook is running, an indicator appears in the bottom-left of the viewer. Changes made during a cook are coalesced: the session finishes what it is doing, then picks up the newest value.
</details>

#### Reading the session panels
<details>
<summary>Instructions</summary>

- **Houdini Console** (bottom-left of the viewer, click the header to expand) — everything Houdini reports: node errors, warnings and messages from your asset, plus client-side events like uploads and exports. This is the first place to look when an asset does not behave as expected.
- **Statistics** (menu bar) — a per-stage breakdown of the last geometry update: how long Houdini cooked, how long the GLB took to write and upload, and how long the browser spent downloading, parsing and drawing it. Handy for working out whether an asset feels slow because of the cook or because of the payload size.
- **Houdini version** (menu bar) — the Houdini build running on the instance. If a loaded asset was authored in a newer build, Houdini reports definition and parameter mismatches while installing it; the session collects those and shows a warning above the parameters. The asset still loads, but may not cook as its author intended.
- **File parameters** — an asset's file parameters get a file picker. The chosen file is uploaded to S3 and downloaded onto the instance before the parameter is set, so the asset can read it as a normal local path.
</details>

#### Understanding HDA outputs
<details>
<summary>Instructions</summary>

**How geometry reaches the viewport**

Session mode never streams geometry out of Houdini directly. Every cook is written to disk as a GLB, uploaded to S3, and handed to the browser as a presigned URL:

1. `session_runner.hip` contains a fixed export pipeline — an Object Merge (`/obj/EXPORT/EXPORT_NODE_REF`) feeding a GLTF ROP (`/obj/EXPORT/EXPORT_GLTF`).
2. When your asset is loaded it is instantiated inside `/obj/CONTAINER`, and one `null` "tap" node is created per output of the asset, each wired to a specific output connector.
3. The Object Merge is aimed at the tap for the output being exported, the ROP renders it to a `.glb`, and the file is uploaded to the session's output bucket.
4. The browser downloads that GLB and loads it into the three.js viewport.

The tap nodes exist because an Object Merge can only pull output 0 of whatever node it is aimed at. Without them, only the first output of your asset would ever be reachable.

**Splitting preview geometry from export geometry**

The viewport recooks on every parameter change, so an asset that only produces its final, expensive result will feel sluggish to work with. Expose more than one output to avoid that:

- **Output 0** — what the viewport shows. Keep it cheap: lower resolution, fewer scatter points, no expensive solvers. This is the only output that cooks while the user drags a slider.
- **Output 1 (and beyond)** — the geometry you actually want delivered. These are **only** cooked when the user exports, so they can be as heavy as the result demands.

Nothing is required of a single-output asset — it behaves exactly as before, with its one output used for both the viewport and export.

**Exporting**

`Scene > Export` on a single-output asset saves that output immediately. On an asset with several outputs it opens a dialog listing them, defaulting to the last one, and the chosen output is cooked and downloaded as `geometry_out<N>_<timestamp>.glb`. Which output was used is written to the Houdini Console panel, both when the request is sent and when the geometry comes back.

Output names in the dialog come from the output labels set in the asset's *Type Properties*. Outputs with no label are listed by index.

**Things to know**

- Every output is exported through the same GLTF ROP, so all exports are GLB regardless of which output they came from.
- A non-preview output has usually never cooked before, so the first export of it pays the full cook cost. The session stays responsive but the download will not be instant — the indicator in the bottom-left names the output while it cooks, and stays up until the file has downloaded.
- After an export the pipeline is pointed back at output 0, so the next parameter change cooks the preview again.
</details>

#### 5. Using the Python Client
<details>
<summary>Instructions</summary>

The Python client can be used for programmatic access or debugging. It speaks the same protocol as the web client, and reads the WebSocket URL from `samples/tf_outputs.json` automatically.

Interactive mode:
```bash
python samples/session_tool_client.py --command interactive --hda-file MyTool.hda

# Commands available in the interactive shell:
>>> load OtherTool.hda
>>> params
>>> param /obj/CONTAINER/user_hda/size 5.0
>>> cook
>>> status
>>> geometry
>>> quit
```
`--hda-file` is optional — leave it off to start an empty session and load an asset later with the `load` command.

Programmatic usage:
```python
import asyncio
from samples.session_tool_client import AuroraSessionClient

async def main():
    client = AuroraSessionClient(
        websocket_url="wss://your-api-id.execute-api.eu-north-1.amazonaws.com/production"
    )
    await client.connect()

    # The receive loop drains the socket and resolves the waits below.
    asyncio.create_task(client.receive_messages())

    await client.start_session()
    await client.wait_until_ready()      # instance boot + Houdini cold start

    await client.load_hda("MyTool.hda")  # uploads to S3, installs, returns the schema
    print(client.parameters["parameters"].keys())

    await client.update_parameter("/obj/CONTAINER/user_hda/size", 5.0)
    await asyncio.sleep(5)               # give the cook a moment to come back
    geometry_url = client.get_last_geometry_url()

    await client.terminate()

asyncio.run(main())
```

> [!IMPORTANT]
> `receive_messages()` has to be running for `wait_until_ready()`, `load_hda()` and geometry URLs to work — it is the only thing reading the socket. Every asset is instantiated at `/obj/CONTAINER/user_hda`, so its parameter paths all start with that prefix; the exact names come from `client.parameters` after `load_hda()`.
</details>

> [!TIP]
> Session logs can be found in [CloudWatch](https://eu-north-1.console.aws.amazon.com/cloudwatch/home?region=eu-north-1#logsV2:log-groups/log-group/$252Faws$252Fec2$252Faurora-session) under the `/aws/ec2/aurora-session` log group.

### Destroying all AWS Infrastructure

> [!WARNING]
> Running the commands found below will destroy all provisioned infrastructure, including files you uploaded to the aurora input and output S3 buckets! <b>This cannot be reversed!</b>


<details>
<summary>Instructions</summary>

To destroy all automatically provisioned infrastructure, you can run the following command:
```bash
python infra/build_util.py --destroy_all
```
You will also need to log into the [AWS console](https://aws.amazon.com/console/), and manually delete all created AMI.
</details>


## Repository map: key files and what they do

- Top-level
  - [environment.yml](environment.yml) - Conda environment for local dev and tooling.
  - [README.md](README.md) - Documentation and usage.

- Build and orchestration

  <i>If you need to modify the envvironment in which Houdini is built and run, these are the relevant files for you.</i> 
  - [infra/build_util.py](infra/build_util.py) - Single entrypoint CLI to:
    - Build Docker images for Houdini.
    - Build the AMI with Packer and minimal infra.
    - Provision the cloud stack with Terraform.
    - Destroy provisioned infra.
  - [infra/docker/houdini/](infra/docker/houdini/) - Docker build context for the Houdini container.
    - [infra/docker/houdini/install_files/houdini_version.json](infra/docker/houdini/install_files/houdini_version.json) - Controls Houdini/Python versions and EULA date used during image build.
    - [infra/docker/houdini/Dockerfile](infra/docker/houdini/Dockerfile) - Sets up the containerized environment in which Houdini is run. 

- Provisioning: AMI build (Packer + minimal Terraform)

  <i>If you want to modify properties of the machine used for the processing is provisioned, these are the relevant files for you.</i>
  - [infra/provisioning/building/provision_ami.tf](infra/provisioning/building/provision_ami.tf) - Minimal network + IAM to let Packer build the AMI.
  - [infra/provisioning/building/provision_batch_ami.pkr.hcl](infra/provisioning/building/provision_batch_ami.pkr.hcl) - Packer template for batch AMI.
  - [infra/provisioning/building/provision_session_ami.pkr.hcl](infra/provisioning/building/provision_session_ami.pkr.hcl) - Packer template for session AMI (includes Vulkan drivers).

- Provisioning: runtime cloud stack (Terraform)

  <i>If you wish to modify how the AWS Infrastructure works or is configured, these are the relevant files for you.</i>
  - [infra/provisioning/deployment/shared_infra.tf](infra/provisioning/deployment/shared_infra.tf) - Shared infrastructure used by both batch and session modes (VPC, subnets, NAT, S3 buckets, IAM, security groups). Defines the `admin_ip_access` variable used to restrict SSH.
  - [infra/provisioning/deployment/provision_batch.tf](infra/provisioning/deployment/provision_batch.tf) - Batch-mode runtime stack (SQS/SNS, Lambda, ECS/EC2 wiring) and resolves the latest batch AMI built by the step above.
  - [infra/provisioning/deployment/provision_session.tf](infra/provisioning/deployment/provision_session.tf) - Session-mode runtime stack (WebSocket API Gateway, Lambda functions, DynamoDB session table, EC2 launch template).
  - [infra/provisioning/deployment/batch/lambda_function.py](infra/provisioning/deployment/batch/lambda_function.py) - Lambda handler that launches a batch EC2 instance from an SQS message.
  - [infra/provisioning/deployment/session/lambda_websocket_handler.py](infra/provisioning/deployment/session/lambda_websocket_handler.py) - Lambda handler for WebSocket connect/disconnect/route messages used by session mode.

- Runtime (what executes on the instance/container)
  
  <i>If you wish to modify what happens when a job gets started on AWS, these are the relevant files for you.</i>

  - Batch mode
    - [runtime/batch/entrypoint.sh](runtime/batch/entrypoint.sh) - Boot-time script that downloads the JobPackage from S3, runs the job, and uploads results.
    - [runtime/batch/run.py](runtime/batch/run.py) - Orchestrates local or in-instance processing based on a work directive JSON.
    - [runtime/batch/runner.sh](runtime/batch/runner.sh) - Convenience runner used by the image/instance.
    - [runtime/batch/processing.py](runtime/batch/processing.py) - Hython script that loads a HIP file per a JSON directive and cooks outputs.
    - [runtime/batch/docker_utils.py](runtime/batch/docker_utils.py) - Helpers used during containerized execution.

  - Session mode
    - [runtime/session/entrypoint.sh](runtime/session/entrypoint.sh) - Boot-time script for interactive session mode (two-process architecture).
    - [runtime/session/houdini_runner.py](runtime/session/houdini_runner.py) - Hython process that loads HDA, processes parameter updates, and exports GLTF from a selected HDA output.
    - [runtime/session/websocket_handler.py](runtime/session/websocket_handler.py) - Pure asyncio WebSocket bridge between API Gateway and the local Houdini runner.
    - [runtime/session/hda_utils.py](runtime/session/hda_utils.py) - Utilities for installing/instantiating HDAs, tapping their outputs into the export pipeline, and extracting parameter schemas.
    - [runtime/session/session_runner.hip](runtime/session/session_runner.hip) - Template HIP file for the session GLTF export pipeline.

  - Shared
    - [runtime/shared/s3/download_file.sh](runtime/shared/s3/download_file.sh) - Downloads the JobPackage from S3.
    - [runtime/shared/s3/upload_file.sh](runtime/shared/s3/upload_file.sh) - Uploads the JobResult back to S3.

- Utilities (shared Python helpers used by build_util and provisioners)
  - [infra/utils/aws_utils.py](infra/utils/aws_utils.py) - AWS account/region helpers used by the CLI.
  - [infra/utils/packer_utils.py](infra/utils/packer_utils.py) - Packer init/run wrappers.
  - [infra/utils/terraform_utils.py](infra/utils/terraform_utils.py) - Terraform init/apply/destroy and outputs.
  - [infra/utils/sesiweb_utils.py](infra/utils/sesiweb_utils.py) - SideFX download metadata lookup.
  - [infra/utils/misc_utils.py](infra/utils/misc_utils.py) - Local admin/elevation checks and misc helpers.
  - [infra/utils/constants.py](infra/utils/constants.py) - Central constants (default AMI name, secret names, etc.).

- Samples and shared data

  <i>These are some files which you can use to try out this sample project without any additional changes required from your end.</i>
  - [samples/JobPackageSample.zip](samples/JobPackageSample.zip) - Example JobPackage (includes a work directive and assets).
  - [samples/send_aurora_request.py](samples/send_aurora_request.py) - Client script to enqueue a batch job with SQS using an S3 URI.
  - [samples/session_tool_client.py](samples/session_tool_client.py) - Python WebSocket client for interactive session mode.
  - [samples/tf_outputs.json](samples/tf_outputs.json) - Generated after provisioning for convenience.

- Web application (Session mode frontend)
  - [webapp/config.js](webapp/config.js) - WebSocket URL and session timeout configuration.
  - [webapp/index.html](webapp/index.html) - Interactive session UI with 3D viewer, parameter controls, and file upload. Markup and bootstrap only; all behaviour lives in [webapp/aurora/app.js](webapp/aurora/app.js).
  - [webapp/session_tool_demo.css](webapp/session_tool_demo.css) - Styling for the session UI.
  - [webapp/aurora/app.js](webapp/aurora/app.js) - Application orchestrator: session lifecycle, menus, cook control, log console, export dialog, and the geometry flow.
  - [webapp/aurora/session.js](webapp/aurora/session.js) - WebSocket client: connection, commands, and S3 uploads via presigned URLs.
  - [webapp/aurora/viewport.js](webapp/aurora/viewport.js) - Three.js viewer that loads the exported GLB.
  - [webapp/aurora/parameters.js](webapp/aurora/parameters.js) - Builds HTML controls from an HDA's parameter schema.
  - [webapp/aurora/events.js](webapp/aurora/events.js) - Minimal event emitter the modules above inherit from.
  - [webapp/gltf_viewer.html](webapp/gltf_viewer.html) - Standalone GLB viewer, useful for inspecting an exported file on its own.

- Keys and infra assets
  - [infra/provisioning/aurora-key-pair.pem](infra/provisioning/aurora-key-pair.pem) - Example path for the EC2 keypair (ensure correct file permissions before use).


## Troubleshooting
- If you run into an error during the building of the AMI, where packer complains about permissions on the `.pem` key (`Permission denied (publickey)`), ensure you "Disable Inheritance" on the `<pem key>` on windows, and change the permissions on Linux with `chmod 400 <pem key>`.
- If you get any errors about resources not existing on AWS, check that the region you used in `aws configure` matches what you use when logged in on AWS console.
- If you run into this error: `Error launching source instance: VcpuLimitExceeded: You have requested more vCPU capacity than your current vCPU limit of 0 allows for the instance bucket that the specified instance type belongs to.` please revisit the [prerequisites](#prerequisites) section at the top of this README referring to ensuring you have sufficient `service quota`.
- If environment variables do not get recognized in your terminal, ensure you created the environment variables in the system section and not the user section.


## Acknowledgements
- [sesiweb](https://github.com/aaronsmithtv/sesiweb) - This is a python module, which makes downloading (specific) Houdini builds from SideFX super easy.
