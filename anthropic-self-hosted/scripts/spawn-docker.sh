#!/bin/bash
# spawn-docker.sh: called by `ant beta:worker poll --on-work` for each session.
# Creates an OpenShell sandbox via the local Docker gateway and runs the
# ant worker session inside it.
#
# Required env vars (set by ant worker):
#   ANTHROPIC_SESSION_ID
#   ANTHROPIC_ENVIRONMENT_KEY
#   ANTHROPIC_WORK_ID
#   ANTHROPIC_ENVIRONMENT_ID
#
# Optional:
#   ANTHROPIC_BASE_URL (defaults to https://api.anthropic.com)
#   OPENSHELL_GATEWAY (defaults to local-docker)
set -euo pipefail

GATEWAY="${OPENSHELL_GATEWAY:-local-docker}"
SANDBOX_NAME="ant-${ANTHROPIC_SESSION_ID:0:8}"
ANT_VERSION="1.9.1"

openshell sandbox create -g "$GATEWAY" --name "$SANDBOX_NAME" --no-auto-providers --no-keep -- \
  sh -c "
    ARCH=\$(uname -m | sed -e 's/x86_64/amd64/' -e 's/aarch64/arm64/')
    wget -qO- 'https://github.com/anthropics/anthropic-cli/releases/download/v${ANT_VERSION}/ant_${ANT_VERSION}_linux_'\${ARCH}'.tar.gz' \
      | tar -xz -C /usr/local/bin ant 2>/dev/null \
      || python3 -c \"
import urllib.request, tarfile, io
url = 'https://github.com/anthropics/anthropic-cli/releases/download/v${ANT_VERSION}/ant_${ANT_VERSION}_linux_amd64.tar.gz'
data = urllib.request.urlopen(url).read()
t = tarfile.open(fileobj=io.BytesIO(data))
t.extract('ant', '/usr/local/bin')
print('ant installed via python')
\"

    export ANTHROPIC_SESSION_ID='$ANTHROPIC_SESSION_ID'
    export ANTHROPIC_ENVIRONMENT_KEY='$ANTHROPIC_ENVIRONMENT_KEY'
    export ANTHROPIC_WORK_ID='$ANTHROPIC_WORK_ID'
    export ANTHROPIC_ENVIRONMENT_ID='$ANTHROPIC_ENVIRONMENT_ID'
    export ANTHROPIC_BASE_URL='${ANTHROPIC_BASE_URL:-https://api.anthropic.com}'
    ant beta:worker run --workdir /workspace
  "
