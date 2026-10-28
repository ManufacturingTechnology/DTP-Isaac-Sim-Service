#!/bin/bash

set -e

# Move to this directory
SCRIPTPATH="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPTPATH"

python3 -m venv venv-linux
venv-linux/bin/pip install *.whl
