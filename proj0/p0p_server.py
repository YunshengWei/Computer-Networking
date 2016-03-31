import socket, threading, sys
from p0p_message import *
from p0p_constant import *


class ServerThreaded:
    def __init__(self, port):
        self.lock = threading.RLock()
        self.port = port
        self.sessions = {}
        self.seq_no = 0
        self.timers = {}
        self.server_socket = None
        self.send_socket = None

    def terminate_session(self, session_id):
        with self.lock:
            t = self.sessions.pop(session_id, None)
            if t:
                address, _ = t
                self.send_msg("GOODBYE", session_id, address)
                self.timers.pop(session_id, None).cancel()

    def send_msg(self, cmd, session_id, address):
        msg = generate_message(cmd, self.seq_no, session_id)
        self.send_socket.sendto(msg, address)
        self.seq_no += 1

    def handle_session(self, session_id, msg):
        address, next_seq_no = self.sessions[session_id]
        if next_seq_no == 0:
            if get_cmd(msg) != "HELLO" or get_seq_no(msg) != 0:
                self.terminate_session(session_id)
            else:
                self.sessions[session_id][1] = 1
                self.send_msg("HELLO", session_id, address)
                self.timers[session_id] = threading.Timer(
                    TIMEOUT_INTERVAL, self.terminate_session, args = (session_id))
                self.timers[session_id].start()
        else:
            if get_cmd(msg) == "GOODBYE":
                self.terminate_session(session_id)
            elif get_cmd(msg) == "DATA":
                self.timers[session_id].cancel()
                self.timers[session_id] = threading.Timer(
                    TIMEOUT_INTERVAL, self.terminate_session, args=(session_id, ))
                self.timers[session_id].start()
                self.send_msg("ALIVE", session_id, address)

                seq_no = get_seq_no(msg)
                if seq_no == next_seq_no - 1:
                    print "0x%08x [%u] Duplicate packet!" % (session_id, seq_no)
                elif seq_no < next_seq_no - 1:
                    self.terminate_session(session_id)
                else:
                    for i in xrange(next_seq_no, seq_no):
                        print "0x%08x [%u] Lost packet!" % (session_id, i)
                    self.sessions[session_id][1] = seq_no + 1
                    payload = get_payload(msg)
                    print "0x%08x [%u] %s" % (session_id, seq_no, payload)
            else:
                self.terminate_session(session_id)

    def handle_msg(self, msg, address):
        with self.lock:
            if not isvalid_message(msg):
                return
            session_id = get_sess_id(msg)
            if session_id not in self.sessions:
                self.sessions[session_id] = [address, 0]
            self.handle_session(session_id, msg)

    def stdin_loop(self):
        while True:
            line = sys.stdin.readline()
            if not line or line.rstrip() == "q":
                break
        with self.lock:
            for session_id in self.sessions.keys():
                self.terminate_session(session_id)
            self.send_socket.close()
            self.server_socket.close()
        sys.exit(0)

    def network_loop(self):
        try:
            while True:
                msg, client_address = self.server_socket.recvfrom(MAX_MESSAGE_LENGTH)
                self.handle_msg(msg, client_address)
        except socket.error, error_msg:
            print "Socket error! %s" % error_msg

    def run(self):
        self.send_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.server_socket.bind(('', self.port))

        threading.Thread(target = self.stdin_loop).start()
        threading.Thread(target = self.network_loop).start()


class ServerAsynchronous:
    def __init__(self, port):
        pass

    def run(self):
        pass


def isvalid_usage(argv):
    return len(argv) == 3 and argv[1] in ["threaded", "asynchronous"]


def show_usage():
    print "Usage:\n\tpython p0p_server.py [threaded|asynchronous] port"
    sys.exit(1)

if __name__ == "__main__":
    if not isvalid_usage(sys.argv):
        show_usage()

    port = int(sys.argv[2])
    if sys.argv[1] == "threaded":
        ServerThreaded(port).run()
    else:
        ServerAsynchronous(port).run()