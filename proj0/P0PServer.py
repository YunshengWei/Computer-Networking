import socket, threading

def run_server_threaded():
    pass

def run_server_asynchronous():
    pass

def run_server_wrapper():
    host = socket.gethostname()
    port = 33333
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    server_socket.bind((host, port))


if __name__ == "__main__":
    run_server_wrapper()