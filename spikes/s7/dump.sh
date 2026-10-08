#!/bin/sh
# Appends the hook or status-line JSON on stdin to $1, one line each.
cat >> "$1"; echo >> "$1"
