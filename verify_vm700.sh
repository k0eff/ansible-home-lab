#!/bin/bash
IP="192.168.31.152"

echo "=== Connectivity Test for $IP (vm700) ==="

echo -n "Pinging $IP... "
if ping -c 1 -W 1 $IP &> /dev/null; then
    echo "OK"
else
    echo "FAILED"
    exit 1
fi

echo "Checking service ports..."

# 9000/9443 Portainer, 6666 RocketChat, 20130 OmniRoute dashboard
for port in 9000 9443 6666 20130; do
    echo -n "Port $port: "
    if nc -z -G 2 $IP $port &> /dev/null; then
        echo "OPEN"
    else
        echo "CLOSED (or filtered)"
    fi
done
