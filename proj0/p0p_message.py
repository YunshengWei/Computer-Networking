import struct

magic = "\xC4\x61"
version = "\x01"


def generate_message(cmd, seq_no, sess_id, payload=""):
    command = {"HELLO": "\x00",
               "DATA": "\x01",
               "ALIVE": "\x02",
               "GOODBYE": "\x03"}[cmd]
    sequence_number = struct.pack("!I", seq_no)
    session_id = struct.pack("!I", sess_id)
    return "".join([magic, version, command, sequence_number, session_id, payload])


def isvalid_message(msg):
    return magic == msg[:2] and version == msg[2]


def get_cmd(msg):
    return {"\x00": "HELLO",
            "\x01": "DATA",
            "\x02": "ALIVE",
            "\x03": "GOODBYE"}[msg[3]]


def get_seq_no(msg):
    return struct.unpack("!I", msg[4:8])[0]


def get_sess_id(msg):
    return struct.unpack("!I", msg[8:12])[0]


def get_payload(msg):
    return msg[12:]