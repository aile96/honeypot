import os
import subprocess
import sys
import time

def run_script(script_path: str) -> int:
    # Se è eseguibile, lanciamolo direttamente; altrimenti usiamo /bin/sh
    if os.access(script_path, os.X_OK):
        cmd = [script_path]
    else:
        cmd = ["/bin/sh", script_path]

    print(f"Eseguo: {' '.join(cmd)}")
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=True)
        if proc.stdout:
            print(proc.stdout, end="")
        if proc.stderr:
            print(proc.stderr, end="", file=sys.stderr)
        print("Script terminato con successo")
        return 0
    except subprocess.CalledProcessError as e:
        print("Errore durante l'esecuzione dello script", file=sys.stderr)
        if e.stdout:
            print(e.stdout, end="")
        if e.stderr:
            print(e.stderr, end="", file=sys.stderr)
        return e.returncode

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

    code = run_script(script_path)

    # Dopo lo script, resta in idle senza fare nulla
    print(f"Controller in idle (exit code script: {code})…")
    while True:
        time.sleep(30)

if __name__ == "__main__":
    main()
