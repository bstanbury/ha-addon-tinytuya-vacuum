#!/usr/bin/with-contenv bashio

export DEVICE_ID=$(bashio::config 'device_id')
export LOCAL_KEY=$(bashio::config 'local_key')
export DEVICE_IP=$(bashio::config 'device_ip')
export PROTOCOL=$(bashio::config 'protocol')
export API_PORT=$(bashio::config 'api_port')

bashio::log.info "Starting TinyTuya Vacuum Controller"
bashio::log.info "Device: ${DEVICE_ID} at ${DEVICE_IP} (protocol ${PROTOCOL})"

exec python3 /server.py