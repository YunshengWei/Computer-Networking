import threading
import socket
import sys
import logging
import re


BUFSIZE = 1024
TIMEOUT = 60

HOST_PATTERN = re.compile("\n[ ]*Host[ ]*:[ ]*([^ \r\n]+)", re.IGNORECASE)
PORT_PATTERN = re.compile(":(\d+)")
HTTPS_PATTERN = re.compile("https://", re.IGNORECASE)
HTTP_HEADER_END_PATTERN = re.compile("(\r\n\r\n)|(\n\n)")
HTTP_LINE_SEP_PATTERN = re.compile("(\r\n)|(\n)")
CONNECTION_PATTERN = re.compile('[ ]*Connection[ ]*:[^\r\n]*(\r\n|\n)', re.IGNORECASE)
PROXY__PATTERN = re.compile("[ ]*Proxy-connection[ ]*:[ ]*keep-alive[ ]*(\r\n|\n)", re.IGNORECASE)
HTTP_VERSION_PATTERN = re.compile("HTTP/1.1")


class WrongHTTPFormatException(Exception):
    def __init(self, value):
        self.value = value

    def __str__(self):
        return repr(self.value)


def read_full_http_header(conn):
    buf = ""
    while not HTTP_HEADER_END_PATTERN.search(buf):
        buf += conn.recv(BUFSIZE)
    return buf


def get_http_first_line(http_header):
    end_pos = HTTP_LINE_SEP_PATTERN.search(http_header).start()
    return http_header[:end_pos]


def get_server_address(http_header):
    match = HOST_PATTERN.search(http_header)
    if not match:
        raise WrongHTTPFormatException("Fail to find Host field")
    t = match.group(1)
    l = t.split(':')
    if len(l) == 2:
        host, port = l
    elif len(l) == 1:
        host = l[0]
        first_line = get_http_first_line(http_header)
        match = PORT_PATTERN.search(first_line)
        if not match:
            if HTTPS_PATTERN.search(first_line):
                port = 443
            else:
                port = 80
        else:
            port = match.group(1)
    else:
        raise WrongHTTPFormatException("Incorrect Host field format")
    return host, int(port)


def modify_http_header(http_header):
    t1, t2 = HTTP_HEADER_END_PATTERN.search(http_header).span()
    header, payload = http_header[:t1] + "\r\n", http_header[t2:]
    header = CONNECTION_PATTERN.sub('', header)
    header += "Connection: close\r\n"
    header = PROXY__PATTERN.sub('Proxy-connection: close', header)
    header = HTTP_VERSION_PATTERN.sub("HTTP/1.0", header)

    return header + "\r\n" + payload


def wait_client(port):
    try:
        server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server_socket.bind(('', port))

        logging.info("Proxy listening on 0.0.0.0:%s" % port)
        server_socket.listen(5)

        while True:
            conn, addr = server_socket.accept()
            logging.debug("Incoming connection from %s:%s" % (addr[0], addr[1]))

            t = threading.Thread(target=handle_client, args=(conn,))
            t.daemon = True
            t.start()

            # Avoid resource leakage, allow them to be garbage collected
            t = None
            conn = None
    except KeyboardInterrupt:
        logging.info("Proxy interrupted by user")
    except socket.error as e:
        logging.error(e)
    finally:
        try:
            server_socket.close()
        except Exception as e:
            logging.debug(e)


def proxy(client_conn, server_conn, tunneling):
    t = threading.Thread(target=fetch_response, args=(server_conn, client_conn, tunneling))
    t.daemon = True
    t.start()

    while True:
        data = client_conn.recv(BUFSIZE)
        if not data:
            break
        server_conn.sendall(data)

    # Below are essential for elegant teardown.
    server_conn.shutdown(socket.SHUT_WR)
    t.join()


def handle_client(client_conn):
    http_header = read_full_http_header(client_conn)
    first_line = get_http_first_line(http_header)
    logging.info(">>> %s", first_line)

    address = get_server_address(http_header)

    try:
        server_conn = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

        # Set timeout here just in case either server or client doesn't close their socket.
        # Theoretically, they should close.
        client_conn.settimeout(TIMEOUT)
        server_conn.settimeout(TIMEOUT)

        if not http_header.startswith("CONNECT"):
            server_conn.connect(address)
            server_conn.sendall(modify_http_header(http_header))
            proxy(client_conn, server_conn, False)
        else:
            try:
                server_conn.connect(address)
            except Exception as e:
                logging.debug(e)
                client_conn.sendall("HTTP/1.1 502 Bad Gateway\r\n\r\n")
                return
            client_conn.sendall("HTTP/1.1 200 OK\r\n\r\n")
            proxy(client_conn, server_conn, True)

    except Exception as e:
        logging.debug(e)
    finally:
        client_conn.close()
        try:
            server_conn.close()
        except Exception as e:
            logging.debug(e)


def fetch_response(server_conn, client_conn, tunneling=False):
    try:
        if not tunneling:
            response_header = read_full_http_header(server_conn)
            response_header = modify_http_header(response_header)
            data = response_header
        else:
            data = server_conn.recv(BUFSIZE)

        while data:
            client_conn.sendall(data)
            data = server_conn.recv(BUFSIZE)
        client_conn.shutdown(socket.SHUT_WR)
    except Exception as e:
        logging.debug(e)


def check_usage():
    if len(sys.argv) != 2:
        print "Usage:\n\tpython http_proxy.py <port number>"
        sys.exit(1)

if __name__ == "__main__":
    check_usage()
    logging.basicConfig(format="%(asctime)s - %(message)s", level=logging.INFO,
                        datefmt="%a, %d %b %Y %H:%M:%S")
    wait_client(int(sys.argv[1]))
