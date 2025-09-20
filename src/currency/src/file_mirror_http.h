#pragma once
#include <string>

// Avvia il watcher che legge il flag da flagd e aggiorna il path esposto
void StartFlagWatcher();

// Avvia il micro server HTTP che espone esattamente il path letto dal flag
void StartFileMirrorHttp();

// (opzionale) utilità per leggere il valore corrente (copia thread-safe)
std::string GetExposedPath();
