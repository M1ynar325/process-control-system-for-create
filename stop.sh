#!/usr/bin/env bash
# 停掉占用 8765/8080 的进程
for p in 8765 8080; do fuser -k -n tcp $p 2>/dev/null && echo "已释放 $p"; done
