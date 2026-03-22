#!/bin/bash

set -e

export DEBIAN_FRONTEND=noninteractive
# Set environment variables for OpenCL and Houdini
export OCL_ICD_VENDORS="/etc/OpenCL/vendors"
export QT_QPA_PLATFORM="offscreen;wayland"
export USER_NAME="ubuntu"
export USER_HOME="/home/${USER_NAME}"

# Install runtime dependencies with options to handle configuration file prompts
# Houdini dependencies
# List based on: https://www.sidefx.com/Support/system-requirements/linux-package-requirements-for-houdini-205/
sudo apt-get update
sudo apt-get install -y --no-install-recommends \
-o Dpkg::Options::="--force-confdef" \
-o Dpkg::Options::="--force-confold" \
curl \
awscli \
sudo \
jq \
libasound2 \
libc6 \
libdbus-1-3 \
libevent-core-2.1-7 \
libexpat1 \
libfontconfig1 \
libgl1 \
libglx0 \
libice6 \
libnspr4 \
libnss3 \
libopengl0 \
libpci3 \
libsm6 \
libx11-6 \
libx11-xcb1 \
libxcb-cursor0 \
libxcb-dri3-0 \
libxcb-icccm4 \
libxcb-image0 \
libxcb-keysyms1 \
libxcb-randr0 \
libxcb-render-util0 \
libxcb-render0 \
libxcb-shape0 \
libxcb-shm0 \
libxcb-sync1 \
libxcb-util1 \
libxcb-xfixes0 \
libxcb-xinerama0 \
libxcb-xkb1 \
libxcb1 \
libxcomposite1 \
libxcursor1 \
libxdamage1 \
libxext6 \
libxfixes3 \
libxi6 \
libxkbcommon-x11-0 \
libxkbcommon0 \
libxrandr2 \
libxrender1 \
libxss1 \
libxt6 \
libxtst6 \
libzstd1 \
zlib1g \
ocl-icd-libopencl1 \
ocl-icd-opencl-dev \
pocl-opencl-icd \
opencl-headers \
clinfo \
libhwloc-dev \
libtinfo5 \
libpocl2 \
libqt5core5a \
libqt5gui5 \
libqt5widgets5 \
libqt5x11extras5
sudo ldconfig


# Read Houdini version configuration
HOUDINI_MAJOR=$(jq -r '.houdini_major' /houdini_tooling/infra/docker/houdini/install_files/houdini_version.json)
HOUDINI_MINOR=$(jq -r '.houdini_minor' /houdini_tooling/infra/docker/houdini/install_files/houdini_version.json)
EULA_DATE=$(jq -r '.eula_date' /houdini_tooling/infra/docker/houdini/install_files/houdini_version.json)

export HOUDINI_USER_PREF_DIR="/home/${USER_NAME}/houdini${HOUDINI_MAJOR}.${HOUDINI_MINOR}"
export AURORA_TOOLING_ROOT=/houdini_tooling

# Create installer directory
sudo mkdir -p /houdini_installer
sudo chown ${USER_NAME}:${USER_NAME} /houdini_installer

# Download and extract Houdini using the script from the repo
cd /houdini_installer
/opt/miniconda/bin/conda run --no-capture-output -n aurora_env python /houdini_tooling/infra/docker/houdini/install_files/download_houdini.py \
  --download-url "${HOUDINI_DOWNLOAD_URL}" \
  --filename "${HOUDINI_DOWNLOAD_FILENAME}" \
  --hash "${HOUDINI_DOWNLOAD_HASH}" \
  --installer-path /houdini_installer/

# Install Houdini
sudo mkdir -p /opt/houdini
sudo chmod 755 /opt/houdini
sudo /houdini_installer/build/houdini.install --auto-install --accept-EULA ${EULA_DATE} --make-dir /opt/houdini

# Create user and add to sudo group
sudo usermod -aG sudo ${USER_NAME}
echo "${USER_NAME} ALL=(ALL) NOPASSWD:ALL" | sudo tee -a /etc/sudoers

# Configure Houdini licensing
sudo mkdir -p /usr/lib/sesi/licenses
sudo mkdir -p ${HOUDINI_USER_PREF_DIR}
echo 'serverhost=https://www.sidefx.com/license/sesinetd' >> ${USER_HOME}/.sesi_licenses.pref
echo 'cd /opt/houdini/ && source /opt/houdini/houdini_setup' >> /home/${USER_NAME}/.bashrc
echo 'cd /opt/houdini/ && source /opt/houdini/houdini_setup' >> /home/${USER_NAME}/.bash_profile

# Required for Qt
sudo mkdir -p /run/user/1000
sudo chown ${USER_NAME}:${USER_NAME} /run/user/1000
sudo chmod 700 /run/user/1000

sudo mkdir -p ${HOUDINI_USER_PREF_DIR}
sudo chown ${USER_NAME}:${USER_NAME} ${HOUDINI_USER_PREF_DIR}
sudo chmod 700 ${HOUDINI_USER_PREF_DIR}
echo "export HOUDINI_USER_PREF_DIR=${HOUDINI_USER_PREF_DIR}" | sudo tee -a /etc/environment

/opt/houdini/python/bin/python3.11 -m pip install boto3 websockets

# Ensure proper ownership
sudo chown -R ${USER_NAME}:${USER_NAME} ${USER_HOME}

# Cleanup
sudo rm -rf /houdini_installer