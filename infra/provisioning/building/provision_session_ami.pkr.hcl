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
  default = "github.com/Ambrosiussen/HoudiniOnAWS.git"
}

variable "tooling_repo_branch" {
  default = "main"
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

# Houdini download information
variable "houdini_download_url" {
}

variable "houdini_download_filename" {
}

variable "houdini_download_hash" {
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

    inline = [
      # Install NVIDIA driver
      "sudo add-apt-repository ppa:graphics-drivers/ppa",
      "sudo apt update",
      "sudo apt install -y nvidia-driver-${var.nvidia_driver_version} nvidia-dkms-${var.nvidia_driver_version}",
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
      "sudo git clone --branch ${var.tooling_repo_branch} https://$GIT_PAT@${var.tooling_repo} --recursive /houdini_tooling",
      "sudo chown -R ubuntu:ubuntu /houdini_tooling",
      "echo 'AURORA_TOOLING_ROOT=/houdini_tooling' | sudo tee -a /etc/environment",

      # Create environment
      "sudo -u ubuntu CONDA_PLUGINS_AUTO_ACCEPT_TOS=yes /opt/miniconda/bin/conda env create -f /houdini_tooling/environment.yml",
    ]
  }

  # Install Houdini using script
  provisioner "shell" {
    environment_vars = [
      "AWS_REGION=${var.aws_region}",
      "HOUDINI_DOWNLOAD_URL=${var.houdini_download_url}",
      "HOUDINI_DOWNLOAD_FILENAME=${var.houdini_download_filename}",
      "HOUDINI_DOWNLOAD_HASH=${var.houdini_download_hash}"
    ]
    script = "${path.root}/scripts/install_houdini.sh"
  }

  # Write Aurora.json package descriptor so Houdini loads third-party packages
  provisioner "shell" {
    inline = [
      # Source environment so HOUDINI_USER_PREF_DIR is available
      "set -a && . /etc/environment && set +a",

      # Create the packages directory
      "sudo mkdir -p $HOUDINI_USER_PREF_DIR/packages",
      "sudo chown ubuntu:ubuntu $HOUDINI_USER_PREF_DIR/packages",

      # Write Aurora.json
      "echo '{\"package_path\": \"$AURORA_TOOLING_ROOT/third_party/\"}' | sudo tee $HOUDINI_USER_PREF_DIR/packages/Aurora.json > /dev/null",
      "sudo chown ubuntu:ubuntu $HOUDINI_USER_PREF_DIR/packages/Aurora.json",

      # Optionally git clone some third-party packages into the tooling's third_party directory, e.g.:
      # "sudo git clone https://github.com/sideeffects/SideFXLabs.git $AURORA_TOOLING_ROOT/third_party/SideFXLabs",
      # Just don't forget to also create a proper package JSON for each plugin in this folder.
      # The path for the plugin should be '/houdini_tooling/third_party/<PLUGIN_FOLDER>'.
    ]
  }


}