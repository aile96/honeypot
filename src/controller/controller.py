import os
import subprocess
import sys
import time
import signal
from typing import Optional

_SHOULD_STOP = False

def _handle_stop(signum, frame):
    global _SHOULD_STOP
    print(f"Ricevuto segnale {signum}, arresto richiesto…")
    _SHOULD_STOP = True

# Registra handler per chiusure pulite (utile in container)
signal.signal(signal.SIGTERM, _handle_stop)
signal.signal(signal.SIGINT, _handle_stop)

def parse_duration(s: Optional[str]) -> Optional[int]:
    """
    Converte una stringa durata in secondi.
    Esempi validi:
      - "30"  -> 30 secondi
      - "45s" -> 45 secondi
      - "5m"  -> 300 secondi
      - "2h"  -> 7200 secondi
    Ritorna None se s è None o stringa vuota.
    Lancia ValueError se il formato è invalido.
    """
    if s is None:
        return None
    s = s.strip().lower()
    if not s:
        return None
    if s.isdigit():
        return int(s)
    # suffissi
    if s.endswith("s"):
        return int(s[:-1])
    if s.endswith("m"):
        return int(float(s[:-1]) * 60)
    if s.endswith("h"):
        return int(float(s[:-1]) * 3600)
    # fallback: prova a interpretare come numero (secondi)
    return int(float(s))

def run_script(script_path: str) -> int:
    # Determina l'interprete
    interpreter = None
    try:
        with open(script_path, "r", encoding="utf-8", errors="ignore") as f:
            first = f.readline().strip()
            if first.startswith("#!"):
                shebang = first[2:].strip()
                if "bash" in shebang:
                    interpreter = "/bin/bash"
                elif "sh" in shebang:
                    interpreter = "/bin/sh"
    except Exception:
        pass

    # Heuristics: .sh => bash, altrimenti fallback a /bin/sh se non eseguibile
    if interpreter is None and script_path.endswith(".sh"):
        interpreter = "/bin/bash"

    if interpreter:
        cmd = [interpreter, script_path]
    else:
        # Se ha shebang valida + eseguibile, lancia diretto; altrimenti /bin/sh
        if os.access(script_path, os.X_OK):
            cmd = [script_path]
        else:
            cmd = ["/bin/sh", script_path]

    print(f"Eseguo: {' '.join(cmd)}")
    # Streamma stdout/stderr (niente buffering), exit code propagato
    proc = subprocess.Popen(cmd)
    try:
        return proc.wait()
    except KeyboardInterrupt:
        proc.terminate()
        return proc.wait()

def main():
    print("Controller di test avviato")

    script_name = os.getenv("SCRIPT_NAME")
    if not script_name:
        print("Variabile d'ambiente SCRIPT_NAME non impostata", file=sys.stderr)
        sys.exit(1)

    script_path = os.path.join("/app/scripts", script_name)
    if not os.path.isfile(script_path):
        print(f"Script non trovato: {script_path}", file=sys.stderr)
        sys.exit(1)

    # Se impostata, esegue periodicamente; se assente/vuota/0, esegue una sola volta
    run_every_raw = os.getenv("RUN_EVERY")  # es: "30s", "5m", "2h", "60"
    try:
        interval = parse_duration(run_every_raw)
    except ValueError:
        print(f"Valore RUN_EVERY non valido: {run_every_raw!r}. Esecuzione singola.", file=sys.stderr)
        interval = None

    if interval and interval > 0:
        print(f"Modalità periodica attiva: esecuzione ogni {interval} secondi.")
        # Esegue subito, poi ripete finché non arriva un segnale di stop
        while not _SHOULD_STOP:
            code = run_script(script_path)
            if _SHOULD_STOP:
                break
            print(f"Attendo {interval} secondi prima della prossima esecuzione (ultimo exit code: {code})…")
            # Attendi in piccoli passi per reagire rapidamente ai segnali
            slept = 0
            while slept < interval and not _SHOULD_STOP:
                time.sleep(min(1, interval - slept))
                slept += 1
        print("Arresto richiesto, controller in uscita.")
    else:
        print("Modalità singola: eseguo lo script una sola volta.")
        code = run_script(script_path)
        # Mantieni il processo vivo (comportamento attuale) oppure esci:
        stay_idle = os.getenv("STAY_IDLE", "1")  # default 1 = rimani in idle
        if stay_idle not in ("0", "false", "False"):
            print(f"Controller in idle (exit code script: {code})… Premi Ctrl+C per terminare.")
            while not _SHOULD_STOP:
                time.sleep(60)
            print("Arresto richiesto, controller in uscita.")
        else:
            sys.exit(code)

if __name__ == "__main__":
    main()
