#!/bin/bash

set -e

# Move to this directory
SCRIPTPATH="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPTPATH"

if [ ! -f "venv-linux/bin/amt-dtp-iscp" ]; then
    echo "Error: venv-linux/bin/amt-dtp-iscp does not exist. Run install.sh first."
    exit 1
fi

venv-linux/bin/amt-dtp-iscp
