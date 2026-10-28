ARG ISAAC_SIM_VERSION
FROM nvcr.io/nvidia/isaac-sim:${ISAAC_SIM_VERSION:-5.1.0}

# Temporarily set to root for permission to install packages
USER 0:0

# Install packages
RUN apt update \
  && apt install -y \
    zip \
    nano \
    python3 \
    python3-venv \
    python3-pip \
  && apt -y autoremove \
  && apt clean autoclean \
  && rm -rf /var/lib/apt/lists/*

# Install extensions
COPY exts/* /isaac-sim/exts/
RUN cd /isaac-sim/exts \
  && unzip *.zip \
  && rm *.zip \
  && chown -R 1234:1234 *


# from https://docs.ros.org/en/jazzy/Installation/Ubuntu-Install-Debs.html

#ARG ROS_VERSION # from https://github.com/ros-infrastructure/ros-apt-source/releases/
#RUN apt update \
#  && apt install -y \
#    software-properties-common \
#    curl \
#  && add-apt-repository universe \
#  && apt update \
#  && curl -L -o /tmp/ros2-apt-source.deb "https://github.com/ros-infrastructure/ros-apt-source/releases/download/${ROS_VERSION}/ros2-apt-source_${ROS_VERSION}.$(. /etc/os-release && echo ${UBUNTU_CODENAME:-${VERSION_CODENAME}})_all.deb" \
#  && dpkg -i /tmp/ros2-apt-source.deb \
#  && apt update \
#  && apt install -y \
#    ros-jazzy-ros-base \
#  && apt -y autoremove \
#  && apt clean autoclean \
#  && rm -rf /var/lib/apt/lists/*

#RUN echo "" >> /isaac-sim/.bashrc \
#  && echo "export isaac_sim_package_path=$HOME/isaacsim" >> /isaac-sim/.bashrc \
#  && echo "export ROS_DISTRO=jazzy" >> /isaac-sim/.bashrc \
#  && echo "export RMW_IMPLEMENTATION=rmw_fastrtps_cpp" >> /isaac-sim/.bashrc \
#  && echo "export LD_LIBRARY_PATH=$LD_LIBRARY_PATH:/isaac-sim/exts/isaacsim.ros2.bridge/jazzy/lib" >> /isaac-sim/.bashrc \
#  && echo "source /opt/ros/jazzy/setup.bash" >> /isaac-sim/.bashrc \
#  && echo "" >> /isaac-sim/.bashrc

# Create volume directories and make the default user the owner
RUN \
     mkdir -p /isaac-sim/.cache \
  && mkdir -p /isaac-sim/.nv/ComputeCache \
  && mkdir -p /isaac-sim/.nvidia-omniverse/logs \
  && mkdir -p /isaac-sim/.nvidia-omniverse/config \
  && mkdir -p /isaac-sim/.local/share/ov/data \
  && mkdir -p /isaac-sim/.local/share/ov/pkg \
  && mkdir -p "/isaac-sim/.local/share/ov/data/Kit/Isaac-Sim Streaming/5.1/" \
  && mkdir -p /files \
  && chown -R 1234:1234 /isaac-sim/.cache \
  && chown -R 1234:1234 /isaac-sim/.nv \
  && chown -R 1234:1234 /isaac-sim/.nvidia-omniverse \
  && chown -R 1234:1234 /isaac-sim/.local \
  && chown -R 1234:1234 /files

# Revert back to default isaac-sim user
USER 1234:1234

# Copy Configuration
COPY ["user.config.json", "/isaac-sim/.local/share/ov/data/Kit/Isaac-Sim Streaming/5.1/"]

ENV AMT_DTP_ISE_CONFIG_PATH=/files/config.json
#ENV ACCEPT_EULA=Y

