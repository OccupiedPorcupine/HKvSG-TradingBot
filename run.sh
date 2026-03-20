#!/bin/bash

# Load environment variables
set -a
source .env
set +a

# Set Python path to the root directory
export PYTHONPATH=.

echo "Initializing APEX production loop..."

# The Immortal Loop (Screen 4 Compliance)
while true; do
    echo "[$(date -u +'%Y-%m-%dT%H:%M:%SZ')] Starting APEX..."
    python src/main.py -portfolio_breakdown true
    
    EXIT_CODE=$?
    echo "[$(date -u +'%Y-%m-%dT%H:%M:%SZ')] APEX process exited with code $EXIT_CODE."
    
    echo "Restarting in 10 seconds. Press Ctrl+C to abort..."
    sleep 10
done
