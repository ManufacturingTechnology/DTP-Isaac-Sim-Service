#!/bin/bash



# Check if running as root (EUID must be 0)
if [ "$EUID" -ne 0 ]; then
  echo "Error: This script must be run using sudo." >&2
  exit 1
fi
if [ -z "${SUDO_USER}" ]; then
  echo "Error: SUDO_USER environment variable is empty, not run using sudo."
  exit 1
fi

# Install if not installed
dpkg -s spacenavd > /dev/null 2>&1
if [ $? -ne 0 ]; then
	echo "installing spacenavd..."
	apt install spacenavd
	if [ $? -ne 0 ]; then
		echo "Error: could not install spacenavd"
		exit 1
	fi
fi

# Exit on error
set -e

devices=$(lsusb | grep --ignore-case "3Dconnexion\|3D Mouse" | tr -s '\n'; exit 0)
if [ -z "${devices}" ]; then
	echo "Error: ensure that a single 3D mouse is connected"
	exit 1
fi
device_count=$(echo "${devices}" | wc -l)
echo ""
echo "Connected Devices (${device_count}):"
echo "${devices}"

if [ ${device_count} -ne 1 ]; then
	echo "Error: ensure that a single 3D mouse is connected"
	exit 1
fi

#echo ""
#echo "Connected Device Info:"
device_ids=$(echo "${devices}" | sed 's/^.*ID //g' | sed 's/ .*$//g')
vendor_id=$(echo "${device_ids}" | sed 's/:.*//g')
product_id=$(echo "${device_ids}" | sed 's/.*://g')
#echo "Device IDs: '${device_ids}'"
#echo "Vendor ID: '${vendor_id}'"
#echo "Product ID: '${product_id}'"

echo ""
echo "Adding USB udev Rule (/etc/udev/rules.d/99-spacemouse.rules):"
if [ -f "/etc/udev/rules.d/99-spacemouse.rules" ]; then
	rm -f "/etc/udev/rules.d/99-spacemouse.rules"
fi
echo "SUBSYSTEM==\"usb\", ATTRS{idVendor}==\"${vendor_id}\", ATTRS{idProduct}==\"${product_id}\", MODE=\"0666\", SYMLINK+=\"spacemouse\"" | sudo tee "/etc/udev/rules.d/99-spacemouse.rules"

echo ""
echo "Unplug the 3D mouse, then plug it back in, then press any key to continue"
read -n 1 -s

echo ""
if [ -d "exts/srl.spacemouse" ]; then
  echo "Already installed"
  exit 0
fi

git clone "https://github.com/NVlabs/spacemouse-extension" "exts/srl.spacemouse"
chown -R ${SUDO_USER}:${SUDO_USER} "exts/srl.spacemouse"



