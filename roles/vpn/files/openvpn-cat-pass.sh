#!/bin/bash

cat /usr/local/openvpn_as/init.log | grep -i "To login please use" | cut -d "\"" -f 4