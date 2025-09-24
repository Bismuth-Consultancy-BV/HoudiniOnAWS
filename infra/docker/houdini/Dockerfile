# FIRST STAGE: Download and Install Houdini
FROM ubuntu:22.04 AS houdini-installer

USER root

ARG DOWNLOAD_URL
ARG DOWNLOAD_FILENAME
ARG DOWNLOAD_HASH
ARG PYTHON_VERSION
ARG EULA_DATE
ENV DEBIAN_FRONTEND=noninteractive

# Copy installation files from the host to the container
COPY /install_files/ /houdini_installer/

# Install all required packages
RUN apt-get update && apt-get install -y --no-install-recommends \
    netcat \
    bc \
    fontconfig \
    wget \
    curl \
    libasound2 \
    libgl1 \
    libglu1 \
    libglu1-mesa \
    libglx0 \
    libice6 \
    libnss3 \
    libopengl0 \
    libpci3 \
    libsm6 \
    libx11-6 \
    libx11-xcb1 \
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
    libxi6 \
    libxkbcommon-x11-0 \
    libxkbcommon0 \
    libxrandr2 \
    libxrender1 \
    libxss1 \
    libxt6 \
    libxtst6 \
    ocl-icd-libopencl1 \
    opencl-headers \
    clinfo \
    xvfb \
    libqt5core5a \
    libqt5gui5 \
    libqt5widgets5 \
    libqt5x11extras5 \
    ocl-icd-opencl-dev \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# Install Python and pip
RUN apt-get update && \
    apt-get install -y software-properties-common && \
    add-apt-repository -y ppa:deadsnakes/ppa && \
    apt-get update && \
    apt-get install -y python${PYTHON_VERSION} python${PYTHON_VERSION}-distutils && \
    curl -O https://bootstrap.pypa.io/get-pip.py && \
    python${PYTHON_VERSION} get-pip.py && \
    python${PYTHON_VERSION} -m pip --version && \
    rm get-pip.py && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# Download and Install Houdini
RUN python${PYTHON_VERSION} -m pip install -r /houdini_installer/install_requirements.txt \
    && python${PYTHON_VERSION} /houdini_installer/download_houdini.py \
    --download-url "${DOWNLOAD_URL}" \
    --filename "${DOWNLOAD_FILENAME}" \
    --hash "${DOWNLOAD_HASH}" \
    --installer-path "/houdini_installer/" \
    && mkdir -p /opt/houdini \
    && chmod 755 /opt/houdini \
    && /houdini_installer/build/houdini.install --auto-install --accept-EULA ${EULA_DATE} --make-dir /opt/houdini \
    && rm -r /houdini_installer

# SECOND STAGE: Final Image
FROM ubuntu:22.04 AS final
ARG PYTHON_VERSION
ARG USER_NAME="houdini_user"
ARG HOUDINI_MAJOR
ARG HOUDINI_MINOR
ENV DEBIAN_FRONTEND=noninteractive

USER root

COPY --from=houdini-installer /opt/houdini /opt/houdini

# Install runtime dependencies with options to handle configuration file prompts
RUN apt-get update && apt-get install -y --no-install-recommends \
    -o Dpkg::Options::="--force-confdef" \
    -o Dpkg::Options::="--force-confold" \
    # Unrelated to Houdini
    curl \
    awscli \
    sudo \
    jq \
    # Houdini dependencies
    # List based on: https://www.sidefx.com/Support/system-requirements/linux-package-requirements-for-houdini-205/
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
    # OpenCL dependencies
    ocl-icd-libopencl1 \
    ocl-icd-opencl-dev \
    pocl-opencl-icd \
    opencl-headers \
    clinfo \
    libhwloc-dev \
    libtinfo5 \
    libpocl2 \
    # Qt dependencies TODO: Check if these are all necessary
    libqt5core5a \
    libqt5gui5 \
    libqt5widgets5 \
    libqt5x11extras5 && \
    apt-get clean && rm -rf /var/lib/apt/lists/* && \
    ldconfig

# Set environment variables for OpenCL and Houdini
ENV OCL_ICD_VENDORS="/etc/OpenCL/vendors" \
    QT_QPA_PLATFORM="offscreen;wayland" \
    HOUDINI_USER_PREF_DIR="/home/${USER_NAME}/houdini${HOUDINI_MAJOR}.${HOUDINI_MINOR}" \
    USER_HOME="/home/${USER_NAME}" 

# Create user and add to sudo group
RUN useradd -m -s /bin/bash ${USER_NAME} && \
    usermod -aG sudo ${USER_NAME} && \
    echo "${USER_NAME} ALL=(ALL) NOPASSWD:ALL" >> /etc/sudoers

# Licensing and final configuration
RUN mkdir -p ${HOUDINI_USER_PREF_DIR} && \
    # Configure Houdini licensing
    mkdir -p /usr/lib/sesi/licenses && \
    touch /usr/lib/sesi/licenses && \
    echo 'serverhost=https://www.sidefx.com/license/sesinetd' >> ${USER_HOME}/.sesi_licenses.pref && \
    echo 'cd /opt/houdini/ && source /opt/houdini/houdini_setup' >> /home/${USER_NAME}/.bashrc && \
    echo 'cd /opt/houdini/ && source /opt/houdini/houdini_setup' >> /home/${USER_NAME}/.bash_profile && \
    # Install system Python packages
    apt-get update && \
    apt-get install -y software-properties-common && \
    add-apt-repository -y ppa:deadsnakes/ppa && \
    apt-get update && \
    apt-get install -y python${PYTHON_VERSION} python${PYTHON_VERSION}-distutils && \
    curl -O https://bootstrap.pypa.io/get-pip.py && \
    # Install pip
    python${PYTHON_VERSION} get-pip.py && \
    rm get-pip.py && \
    # Clean up
    apt-get clean && rm -rf /var/lib/apt/lists/* && \
    # Required for Qt, otherwise will run into permission issues
    mkdir -p /run/user/1000 && \
    # Ensure proper ownership
    chown -R ${USER_NAME}:${USER_NAME} ${USER_HOME} && \
    chown ${USER_NAME}:${USER_NAME} /run/user/1000 && \
    chmod 700 /run/user/1000

USER ${USER_NAME}
WORKDIR ${USER_HOME}

# Install Python dependencies
COPY /install_files/ /houdini_installer/
RUN python${PYTHON_VERSION} -m pip install -r /houdini_installer/runtime_requirements.txt && \
    # Clean up
    sudo rm -r /houdini_installer

ENTRYPOINT ["/bin/bash", "-l"]