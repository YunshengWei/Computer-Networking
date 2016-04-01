import threading
import socket
import sys
import logging
import re


BUFSIZE = 1024
TIMEOUT = 10

HOST_PATTERN = re.compile("\n[ ]*Host[ ]*:[ ]*([^ \r\n]+)", re.IGNORECASE)
PORT_PATTERN = re.compile(":(\d+)")
HTTPS_PATTERN = re.compile("https://", re.IGNORECASE)
HTTP_HEADER_END_PATTERN = re.compile("(\r\n\r\n)|(\n\n)")
HTTP_LINE_SEP_PATTERN = re.compile("(\r\n)|(\n)")
CONNECTION_PATTERN = re.compile('[ ]*Connection[ ]*:[^\r\n]*(\r\n|\n)', re.IGNORECASE)
PROXY__PATTERN = re.compile("[ ]*Proxy-connection[ ]*:[ ]*keep-alive[ ]*(\r\n|\n)", re.IGNORECASE)

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
    return host, port


def modify_http_header(http_header):
    t1, t2 = HTTP_HEADER_END_PATTERN.search(http_header).span()
    header, payload = http_header[:t1] + "\r\n", http_header[t2:]
    header = CONNECTION_PATTERN.sub('', header)
    header += "Connection: close\r\n"
    header = PROXY__PATTERN.sub('Proxy-connection: close', header)

    return header + "\r\n" + payload


def wait_client(port):
    try:
        server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server_socket.bind(('', port))

        logging.info("Proxy listening on 0.0.0.0:%s" % port)
        server_socket.listen(5)

        while True:
            conn, addr = server_socket.accept()
            #logging.debug("Incoming connection from %s:%s" % (addr[0], addr[1]))

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


def handle_client(client_conn):
    try:
        client_conn.settimeout(TIMEOUT)

        http_header = read_full_http_header(client_conn)
        first_line = get_http_first_line(http_header)
        logging.info(">>> %s", first_line)

        http_header = modify_http_header(http_header)

        host, port = get_server_address(http_header)
        server_conn = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server_conn.settimeout(TIMEOUT)
        server_conn.connect((host, port))

        t = threading.Thread(target=fetch_response, args=(server_conn, client_conn))
        t.daemon = True
        t.start()

        data = http_header
        while data:
            server_conn.sendall(data)
            data = client_conn.recv(BUFSIZE)

    except Exception as e:
        logging.debug(e)
    finally:
        client_conn.close()
        try:
            server_conn.close()
        except Exception as e:
            logging.debug(e)


def fetch_response(server_conn, client_conn):
    try:
        response_header = read_full_http_header(server_conn)
        response_header = modify_http_header(response_header)
        data = response_header

        while data:
            client_conn.sendall(data)
            data = server_conn.recv(BUFSIZE)

    except Exception as e:
        logging.debug(e)
    finally:
        server_conn.close()
        client_conn.close()


def check_usage():
    if len(sys.argv) != 2:
        print "Usage:\n\tpython http_proxy.py <port number>"
        sys.exit(1)

if __name__ == "__main__":
    check_usage()
    logging.basicConfig(format="%(asctime)s - %(message)s", level=logging.DEBUG,
                        datefmt="%a, %d %b %Y %H:%M:%S")
    wait_client(int(sys.argv[1]))
