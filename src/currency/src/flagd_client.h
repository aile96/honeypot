#pragma once
#include <string>
#include <optional>

// Risolve una stringa da flagd via HTTP (ResolveString).
// Ritorna std::nullopt se non disponibile/errore.
std::optional<std::string> FlagdResolveString(
    const std::string& host,
    int port,
    const std::string& flag_key);
