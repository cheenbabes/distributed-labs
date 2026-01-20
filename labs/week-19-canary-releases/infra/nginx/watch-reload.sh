#!/bin/sh
# Watch for changes to upstream.conf and reload nginx

CONFIG_DIR="/etc/nginx/conf.d"
CONFIG_FILE="upstream.conf"

echo "Starting nginx config watcher on ${CONFIG_DIR}/${CONFIG_FILE}"

while true; do
    # Wait for modify events on the config directory
    inotifywait -e modify,create,delete -q "${CONFIG_DIR}"

    echo "Config change detected, testing nginx config..."

    # Test the configuration before reloading
    if nginx -t 2>&1; then
        echo "Config valid, reloading nginx..."
        nginx -s reload
        echo "Nginx reloaded successfully"
    else
        echo "ERROR: Invalid nginx config, not reloading"
    fi

    # Small delay to avoid rapid reloads
    sleep 1
done
