# This configuration requires you to have a secret in AWS Secrets Manager named `GithubCredentials` with a JSON structure containing a `PAT` key.

# NVIDIA driver version
variable "nvidia_driver_version" {
  default = "550"
}

# VULKAN sdk version
variable "vulkan_version" {
  default = "1.3.280"
}

# Storage capacity in Gb for the AMI being built
variable "provisioning_storage_capacity" {
  default = 50
}

# Link to Github repository containing the tooling.
# No https:// prefix is required, since a PAT will be prefixed to authenticate.
variable "tooling_repo" {
  default = "github.com/Bismuth-Consultancy-BV/HoudiniOnAWS.git"
}

# Instance type for the EC2 instance used for building the AMI
variable "instance_type" {
  default = "g4dn.2xlarge"
}

# The variables below are all passed in through CLI

packer {
  required_plugins {
    amazon = {
      version = ">= 1.2.8"
      source  = "github.com/hashicorp/amazon"
    }
  }
}

# Pre-existing VPC ID used for building AMI
variable "vpc_id" {
  # default = "vpc-0a7a4e53fd0a1b441"
}

# Pre-existing Subnet ID used for building AMI
variable "subnet_id" {
}

# Pre-existing Security Group ID for building AMI
variable "security_group_id" {
}

# The IAM role used for provisioning the AMI.
variable "provisioning_iam_role" {
}

# Remote keypair name
variable "keypair_name" {
}

# AWS region to deploy on
variable "aws_region" {
}

# Local provisioning keypair location
variable "keypair_path" {
}

# Name of the to be built AMI
variable "ami_name" {
}

source "amazon-ebs" "marketplace_ami" {
  ami_name             = var.ami_name
  region               = var.aws_region
  instance_type        = var.instance_type
  iam_instance_profile = var.provisioning_iam_role

  source_ami_filter {
    filters = {
      name                = "ubuntu/images/hvm-ssd/ubuntu-jammy-22.04-amd64-server-*"
      root-device-type    = "ebs"
      virtualization-type = "hvm"
    }
    most_recent = true
    owners      = ["099720109477"]
  }

  launch_block_device_mappings {
    device_name           = "/dev/sda1"
    volume_size           = var.provisioning_storage_capacity
    volume_type           = "gp3"
    delete_on_termination = true
  }

  ssh_username = "ubuntu"

  vpc_id                      = var.vpc_id
  subnet_id                   = var.subnet_id
  security_group_id           = var.security_group_id
  associate_public_ip_address = true
  ssh_keypair_name            = var.keypair_name
  ssh_private_key_file        = var.keypair_path

  # Overwrite existing AMI
  force_deregister      = true
  force_delete_snapshot = true
}

variable "nvidia_runtime" {
  default = <<EOF
{
  "runtimes": {
    "nvidia": {
      "path": "nvidia-container-runtime",
      "runtimeArgs": []
      
    }
  }
}
EOF
}



build {
  sources = ["source.amazon-ebs.marketplace_ami"]

  # Install initial dependencies
  provisioner "shell" {
    inline = [
      # Install Git
      "sudo apt update",
      "sudo apt install -y jq awscli curl git git-lfs unzip zip",
    ]
  }

  # First we install the NVIDIA driver, since this requires a reboot.
  provisioner "shell" {

    expect_disconnect = true
    pause_after       = "5m"

    inline = [
      # Install NVIDIA driver
      "sudo add-apt-repository ppa:graphics-drivers/ppa",
      "sudo apt update",
      "sudo apt install -y nvidia-driver-${var.nvidia_driver_version} nvidia-dkms-${var.nvidia_driver_version}",

      # Trigger a reboot to complete driver install
      "echo 'Triggering reboot via AWS CLI'",
      "INSTANCE_ID=$(curl -s http://169.254.169.254/latest/meta-data/instance-id)",
      "REGION=$(curl -s http://169.254.169.254/latest/dynamic/instance-identity/document | jq -r .region)",
      "aws ec2 reboot-instances --instance-ids \"$INSTANCE_ID\" --region \"$REGION\"",
    ]
  }

  # Next we install Docker
  provisioner "shell" {

    start_retry_timeout = "15m"
    inline = [
      # Add Docker's official GPG key
      "sudo install -m 0755 -d /etc/apt/keyrings",
      "sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc",
      "sudo chmod a+r /etc/apt/keyrings/docker.asc",

      # Add Docker repository
      "echo \"deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(lsb_release -sc) stable\" | sudo tee /etc/apt/sources.list.d/docker.list",

      # Wait for the dpkg lock to be released
      "while sudo fuser /var/lib/dpkg/lock-frontend >/dev/null 2>&1; do echo 'Waiting for dpkg lock to be released...'; sleep 5; done",

      # Install Docker
      "sudo apt-get update",
      "sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin",

      # Add user to Docker group
      "sudo usermod -aG docker ubuntu",

      # Restart Docker and grant permissions
      "sudo systemctl restart docker",
      "sudo chmod 666 /var/run/docker.sock",

      # Enable Docker on startup
      "sudo systemctl enable docker",
      "sudo systemctl start docker",

      # Install NVIDIA Container Toolkit
      "distribution=$(. /etc/os-release;echo $ID$VERSION_ID)",
      "curl -s -L https://nvidia.github.io/nvidia-docker/gpgkey | sudo apt-key add -",
      "curl -s -L https://nvidia.github.io/nvidia-docker/$distribution/nvidia-docker.list | sudo tee /etc/apt/sources.list.d/nvidia-docker.list",
      "while sudo fuser /var/lib/dpkg/lock-frontend >/dev/null 2>&1; do echo 'Waiting for dpkg lock to be released...'; sleep 5; done",
      "sudo apt-get update",
      "sudo apt-get install -y nvidia-container-toolkit",

      # NVIDIA RUNTIME for Docker
      "sudo mkdir -p /etc/docker",
      "echo '${var.nvidia_runtime}' | sudo tee /etc/docker/daemon.json > /dev/null",
      "sudo docker --config /etc/docker info || echo 'Docker config check failed!'",
      "sudo systemctl restart docker",
      "sudo mkdir -p /root/.docker",
    ]
  }

  # Install Vulkan SDK
  provisioner "shell" {
    inline = [
      # "sudo apt-get install -y --no-install-recommends vulkan-tools libvulkan1",
      "sudo wget -qO - https://packages.lunarg.com/lunarg-signing-key-pub.asc | sudo tee /etc/apt/trusted.gpg.d/lunarg.asc",
      "echo /etc/apt/sources.list.d/lunarg-vulkan-${var.vulkan_version}-jammy.list https://packages.lunarg.com/vulkan/${var.vulkan_version}/lunarg-vulkan-${var.vulkan_version}-jammy.list",
      "sudo wget -qO /etc/apt/sources.list.d/lunarg-vulkan-${var.vulkan_version}-jammy.list https://packages.lunarg.com/vulkan/${var.vulkan_version}/lunarg-vulkan-${var.vulkan_version}-jammy.list",
      "sudo apt update",
      "sudo apt install -y --no-install-recommends vulkan-sdk xvfb",
      "sudo ldconfig",
    ]
  }

  # Install CloudWatch for Logging
  provisioner "shell" {
    inline = [
      # Cloudwatch Agent installation
      "sudo wget https://amazoncloudwatch-agent-${var.aws_region}.s3.${var.aws_region}.amazonaws.com/ubuntu/amd64/latest/amazon-cloudwatch-agent.deb",
      "sudo dpkg -i -E ./amazon-cloudwatch-agent.deb",
    ]
  }


  provisioner "shell" {
    inline = [
      # Install Miniconda system-wide
      "wget https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh -O miniconda.sh",
      "sudo bash miniconda.sh -b -p /opt/miniconda",
      "rm miniconda.sh",
      "echo 'source /opt/miniconda/etc/profile.d/conda.sh' | sudo tee -a /etc/bash.bashrc",

      # Initialize conda for all users
      "sudo /opt/miniconda/bin/conda init bash",

      # Set proper permissions
      "sudo chown -R root:root /opt/miniconda",
      "sudo chmod -R 755 /opt/miniconda",
    ]
  }

  # Clone tooling repo and build dependencies
  provisioner "shell" {
    inline = [
      # Retrieve the secret from AWS Secrets Manager
      "GIT_SECRET=$(aws secretsmanager get-secret-value --secret-id GithubCredentials --region ${var.aws_region} --query 'SecretString' --output text)",
      "GIT_PAT=$(echo $GIT_SECRET | jq -r .PAT)",

      # Clone tooling repo and set environment variable
      "sudo git clone https://$GIT_PAT@${var.tooling_repo} --recursive /houdini_tooling",
      "sudo chown -R ubuntu:ubuntu /houdini_tooling",
      "echo 'AURORA_TOOLING_ROOT=/houdini_tooling' | sudo tee -a /etc/environment",

      # Create environment
      "sudo -u ubuntu CONDA_PLUGINS_AUTO_ACCEPT_TOS=yes /opt/miniconda/bin/conda env create -f /houdini_tooling/environment.yml",

      # Build using the environment
      "sudo -u ubuntu AURORA_TOOLING_ROOT=/houdini_tooling /opt/miniconda/bin/conda run --no-capture-output -n aurora_env python /houdini_tooling/infra/build_util.py --build_images",
    ]
  }

  # # Unreal Engine specific stub code. See description in README on what to do with this.
  # provisioner "shell" {
  #   inline = [
  #     # Logging in on GHCR to pull the UE docker image.
  #     "UE_SECRET=$(aws secretsmanager get-secret-value --secret-id UnrealAccessToken --region ${var.aws_region} --query 'SecretString' --output text)",
  #     "UE_USERNAME=$(echo $UE_SECRET | jq -r .username)",
  #     "UE_PASSWORD=$(echo $UE_SECRET | jq -r .password)",
  #     "echo $UE_PASSWORD | docker login ghcr.io -u $UE_USERNAME --password-stdin",

  #     # Pulling the Unreal Engine Docker image
  #     "docker pull ghcr.io/epicgames/unreal-engine:dev-slim-5.6.0"
  #   ]
  # }

  # Reboot to ensure all installed dependencies are clean.
  provisioner "shell" {

    expect_disconnect = true
    pause_after       = "5m"

    inline = [
      "echo 'Triggering reboot via AWS CLI'",
      "INSTANCE_ID=$(curl -s http://169.254.169.254/latest/meta-data/instance-id)",
      "REGION=$(curl -s http://169.254.169.254/latest/dynamic/instance-identity/document | jq -r .region)",
      "aws ec2 reboot-instances --instance-ids \"$INSTANCE_ID\" --region \"$REGION\"",
    ]
  }


}