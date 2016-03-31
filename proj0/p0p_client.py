import threading
import thread
import sys
import socket
import random
import abc
import signal
import pyuv
import time
from p0p_message import *
from p0p_constant import *


class P0PClientFSM:
    def __init__(self, thread_safe, set_timer_callback,
                 cancel_timer_callback, send_message_callback,
                 close_callback):
        self.thread_safe = thread_safe
        if thread_safe:
            self.lock = threading.Lock()
        self.session_id = random.randint(0, 2 ** 32 - 1)
        self.state = self._hello_wait
        self.sequence_number = 0

        def send_message_wrapper(cmd, payload=""):
            send_message_callback(generate_message(
                cmd, self.sequence_number, self.session_id, payload))
            self.sequence_number += 1

        def set_timer_wrapper():
            set_timer_callback(lambda: self.update(("Timeout", None)))

        self.send_message = send_message_wrapper
        self.set_timer = set_timer_wrapper
        self.cancel_timer = cancel_timer_callback
        self.close = close_callback

        self.send_message("HELLO")
        self.set_timer()
        self.state = self._hello_wait

    def update(self, event):
        if self.thread_safe:
            with self.lock:
                self._update(event)
        else:
            self._update(event)

    def _update(self, event):
        event_type, payload = event
        if event_type == "GOODBYE":
            self.close()
        else:
            self.state(event)

    def _hello_wait(self, event):
        event_type, payload = event
        if event_type == "HELLO":
            self.cancel_timer()
            self.state = self._ready
        elif event_type == "Timeout" or event_type == "eof":
            self.send_message("GOODBYE")
            self.set_timer()
            self.state = self._closing
        else:
            raise ValueError("Unexpected event type '%s' at state '%s'" % (event_type, "hello_wait"))

    def _ready(self, event):
        event_type, payload = event
        if event_type == "ALIVE":
            pass
        elif event_type == "eof":
            self.send_message("GOODBYE")
            self.set_timer()
            self.state = self._closing
        elif event_type == "stdin":
            self.send_message("DATA", payload)
            self.set_timer()
            self.state = self._ready_timer
        else:
            raise ValueError("Unexpected event type '%s' at state '%s'" % (event_type, "ready"))

    def _ready_timer(self, event):
        event_type, payload = event
        if event_type == "stdin":
            self.send_message("DATA", payload)
        elif event_type == "ALIVE":
            self.cancel_timer()
            self.state = self._ready
        elif event_type == "Timeout" or event_type == "eof":
            self.send_message("GOODBYE")
            self.set_timer()
            self.state = self._closing
        else:
            raise ValueError("Unexpected event type '%s' at state '%s'" % (event_type, "ready_timer"))

    def _closing(self, event):
        event_type, payload = event
        if event_type == "ALIVE":
            pass
        elif event_type == "Timeout":
            self.close()
        else:
            raise ValueError("Unexpected event type '%s' at state '%s'" % (event_type, "closing"))


class P0PClientBase(object):
    __metaclass__ = abc.ABCMeta

    def __init__(self, server_address):
        self.server_address = server_address

    @abc.abstractmethod
    def set_timer(self, callback):
        pass

    @abc.abstractmethod
    def cancel_timer(self):
        pass

    @abc.abstractmethod
    def send_message(self, msg):
        pass

    @abc.abstractmethod
    def close(self):
        pass

    @abc.abstractmethod
    def run(self):
        pass


class P0PClientThreaded(P0PClientBase):
    def __init__(self, server_address):
        super(P0PClientThreaded, self).__init__(server_address)
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.timer = None
        self.fsm = None

    def set_timer(self, callback):
        self.timer = threading.Timer(TIMEOUT_INTERVAL, callback)
        self.timer.daemon = True
        self.timer.start()

    def cancel_timer(self):
        self.timer.cancel()

    def send_message(self, msg):
        self.socket.sendto(msg, self.server_address)

    def close(self):
        #self.socket.close()
        thread.interrupt_main()

    def run(self):
        try:
            self.fsm = P0PClientFSM(True, self.set_timer,
                                    self.cancel_timer, self.send_message, self.close)
            t1 = threading.Thread(target=self.stdin_loop)
            t1.daemon = True
            t2 = threading.Thread(target=self.network_loop)
            t2.daemon = True

            t1.start()
            t2.start()

            while threading.active_count() > 1:
                time.sleep(0.1)
        except KeyboardInterrupt:
            sys.exit(0)

    def network_loop(self):
        while True:
            msg = self.socket.recv(MAX_MESSAGE_LENGTH)
            event = (get_cmd(msg), None)
            self.fsm.update(event)

    def stdin_loop(self):
        while True:
            line = sys.stdin.readline().rstrip()
            if not line or line.rstrip() == "q":
                self.fsm.update(("eof", None))
                break
            self.fsm.update(("stdin", line))


class P0PClientAsynchronous(P0PClientBase):
    def __init__(self, server_address):
        super(P0PClientAsynchronous, self).__init__(server_address)
        self.fsm = None
        self.timer = None

        self.loop = pyuv.Loop.default_loop()

        self.tty_stdin = pyuv.TTY(self.loop, sys.stdin.fileno(), True)
        self.tty_stdin.start_read(self.on_tty_read)

        self.udp_handle = pyuv.UDP(self.loop)
        self.udp_handle.start_recv(self.on_udp_read)

        self.signal_h = pyuv.Signal(self.loop)
        self.signal_h.start(self.close, signal.SIGINT)

    def set_timer(self, callback):
        self.timer = pyuv.Timer(self.loop)
        self.timer.start(lambda timer_handle: callback(), TIMEOUT_INTERVAL, False)

    def cancel_timer(self):
        self.timer.stop()

    def send_message(self, msg):
        self.udp_handle.send(self.server_address, msg)

    def close(self):
        self.tty_stdin.close()
        self.udp_handle.close()
        self.timer.close()
        self.signal_h.close()

    def on_tty_read(self, handle, data, error):
        if data is None or (sys.stdin.isatty() and data.rstrip() == 'q'):
            self.fsm.update(("eof", None))
        else:
            self.fsm.update(("stdin", data.rstrip()))

    def on_udp_read(self, handle, ip_port, flags, data, error):
        if data is not None:
            event = (get_cmd(data), None)
            self.fsm.update(event)

    def run(self):
        self.fsm = P0PClientFSM(False, self.set_timer,
                                self.cancel_timer, self.send_message, self.close)

        self.loop.run()

        pyuv.TTY.reset_mode()


def check_usage(argv):
    if len(argv) != 4 or argv[1] not in ["threaded", "asynchronous"]:
        print "Usage:\n\tpython p0p_client.py [threaded|asynchronous] <hostname> <portnum>"
        sys.exit(1)

if __name__ == "__main__":
    check_usage(sys.argv)

    server_address = (sys.argv[2], int(sys.argv[3]))
    if sys.argv[1] == "threaded":
        P0PClientThreaded(server_address).run()
    else:
        P0PClientAsynchronous(server_address).run()