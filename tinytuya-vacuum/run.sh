#!/bin/sh
set -e

CONFIG=/data/options.json

if [ ! -f "$CONFIG" ]; then
    echo "[ERROR] No config file found at $CONFIG"
    exit 1
fi

export DEVICE_ID=$(python3 -c "import json; print(json.load(open('$CONFIG'))['device_id'])")
export LOCAL_KEY=$(python3 -c "import json; print(json.load(open('$CONFIG'))['local_key'])")
export DEVICE_IP=$(python3 -c "import json; print(json.load(open('$CONFIG'))['device_ip'])")
export PROTOCOL=$(python3 -c "import json; print(json.load(open('$CONFIG'))['protocol'])")
export API_PORT=$(python3 -c "import json; print(json.load(open('$CONFIG'))['api_port'])")

echo "[INFO] TinyTuya Vacuum Controller v0.3.0"
echo "[INFO] Device: ${DEVICE_ID} at ${DEVICE_IP} (protocol ${PROTOCOL})"
echo "[INFO] API port: ${API_PORT}"

exec python3 /app/server.py
