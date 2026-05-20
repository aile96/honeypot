#pragma once
#include <string>

// Start the watcher that reads the flag from flagd and updates the exposed path
void StartFlagWatcher();

// Start the micro HTTP server that exposes exactly the path read from the flag
void StartFileMirrorHttp();

// (optional) utility to read the current value (thread-safe copy)
std::string GetExposedPath();
