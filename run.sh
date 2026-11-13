#!/usr/bin/env bash
# 启动 PCS bridge。用法：bash run.sh    （Ctrl-C 停）
cd "$(dirname "$0")"
exec python3 bridge.py
