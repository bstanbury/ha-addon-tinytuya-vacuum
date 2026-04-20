#!/bin/sh
set -e

# Read config from /data/options.json
CONFIG=/data/options.json

export DEVICE_ID=$(cat $CONFIG | python3 -c "import sys,json; print(json.load(sys.stdin)['device_id'])")
export LOCAL_KEY=$(cat $CONFIG | python3 -c "import sys,json; print(json.load(sys.stdin)['local_key'])")
export DEVICE_IP=$(cat $CONFIG | python3 -c "import sys,json; print(json.load(sys.stdin)['device_ip'])")
export PROTOCOL=$(cat $CONFIG | python3 -c "import sys,json; print(json.load(sys.stdin)['protocol'])")
export API_PORT=$(cat $CONFIG | python3 -c "import sys,json; print(json.load(sys.stdin)['api_port'])")

echo "[INFO] Starting TinyTuya Vacuum Controller"
echo "[INFO] Device: ${DEVICE_ID} at ${DEVICE_IP} (protocol ${PROTOCOL})"
echo "[INFO] API port: ${API_PORT}"

exec python3 /server.py
