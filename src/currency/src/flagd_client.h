#pragma once
#include <string>
#include <optional>

// Resolve a string from flagd via HTTP (ResolveString).
// Returns std::nullopt if unavailable/error.
std::optional<std::string> FlagdResolveString(
    const std::string& host,
    int port,
    const std::string& flag_key);
