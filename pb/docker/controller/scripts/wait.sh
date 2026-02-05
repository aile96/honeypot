#!/usr/bin/env bash

TARGET="/tmp/start"

echo "Waiting for file $TARGET to be created..."

# Infinite loop until the file exists
while [ ! -f "$TARGET" ]; do
    # Wait 60 seconds before checking again
    sleep 60
done

echo "File $TARGET has been found!"