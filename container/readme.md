# AMT Digital Testbed Platform (DTP) Isaac Sim Container Deployment (ISCD)

> **NVIDIA's terms.** The image is built on NVIDIA's Isaac Sim container (`nvcr.io/nvidia/isaac-sim`), so it contains
> NVIDIA software (Isaac Sim, Omniverse Kit) under NVIDIA's terms. As of 2026-10-27, the container's NGC page applies the
> [NVIDIA Isaac Sim Additional Software and Materials License](https://www.nvidia.com/en-us/agreements/enterprise-software/isaac-sim-additional-software-and-materials-license/),
> which licenses the software for installation and use by you and your authorized users and grants no right to
> distribute it (its sections 1, 2.2 and 3); NVIDIA's
> [license FAQ](https://docs.isaacsim.omniverse.nvidia.com/latest/common/license-faq.html) says that redistributing Isaac
> Sim with Kit to third parties requires an NVIDIA AI Enterprise license. NVIDIA's Omniverse terms changed during 2026,
> so check the current terms before sharing a built image. Running the container
> requires accepting NVIDIA's license: set `ACCEPT_EULA=Y` in your environment (e.g. `docker-compose.yml`) once you have
> read it.

# Linux

```bash
make
docker compose up -d
```

It will take a while to start the first time, needs to compile shaders or something.


# Windows

****Can't figure out how to get this to work on windows. Just use linux. This is just notes so far.

1. Install WSL

In a `Command Prompt`, remove an existing WSL distro if needed.
```
wsl --unregister <name>
```

In a `Command Prompt`, install debian distro, set it as default, list distros, then run the distro.
```
wsl --install Debian
wsl --set-default Debian
wsl --list
wsl
```

Install [Docker Engine](https://docs.docker.com/engine/install/debian/):
```bash
# Update debian
sudo apt update
sudo apt upgrade -y

# Add Docker's official GPG key:
sudo apt update
sudo apt install -y ca-certificates curl
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc

# Add the repository to Apt sources:
sudo tee /etc/apt/sources.list.d/docker.sources <<EOF
Types: deb
URIs: https://download.docker.com/linux/debian
Suites: $(. /etc/os-release && echo "$VERSION_CODENAME")
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
EOF

sudo apt update

# Install Packages
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

# Add the user to the docker group so you can run docker commands without sudo
sudo usermod -aG docker "${USER}"

exit
```

Start `Docker Desktop` application. You may need to close and restart `Docker Desktop` and the wsl distro (`sudo reboot`).

Run a test image and check connection to docker daemon:
```bash
docker run --rm -it hello-world
docker ps
```

Install the [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html#with-apt-ubuntu-debian):
```bash
# Install packages
sudo apt-get update && sudo apt-get install -y --no-install-recommends ca-certificates curl gnupg2
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg \
  && curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list | \
    sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' | \
    sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list
sudo sed -i -e '/experimental/ s/^#//g' /etc/apt/sources.list.d/nvidia-container-toolkit.list
sudo apt-get update
sudo apt-get install -y nvidia-container-toolkit nvidia-container-toolkit-base libnvidia-container-tools libnvidia-container1

# Configure docker to use nvidia runtime
sudo nvidia-ctk runtime configure --runtime=docker

# Restart service
sudo service docker restart
```

Test gpu passthrough:
```bash
docker run --gpus all nvidia/cuda:12.8.1-base-ubuntu24.04 nvidia-smi
docker run --gpus all nvidia/cuda:13.2.0-base-ubuntu24.04 nvidia-smi
```

Start the stack and check status
```bash
make
docker compose up -d
docker logs -f amt-dtp-ise
docker logs amt-dtp-ise
```