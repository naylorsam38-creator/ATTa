"""Run a command inside the current (fresh) network namespace after bringing loopback up.
Used as:  unshare -n python -I netns_exec.py -- CMD ...   (stdlib only; no iproute2 needed)."""
import fcntl
import os
import socket
import struct
import sys

SIOCGIFFLAGS, SIOCSIFFLAGS, IFF_UP = 0x8913, 0x8914, 0x1


def loopback_up() -> None:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    flags = struct.unpack("16sh", fcntl.ioctl(s, SIOCGIFFLAGS, struct.pack("16sh", b"lo", 0)))[1]
    fcntl.ioctl(s, SIOCSIFFLAGS, struct.pack("16sh", b"lo", flags | IFF_UP))
    s.close()


if __name__ == "__main__":
    args = sys.argv[1:]
    if args and args[0] == "--":
        args = args[1:]
    loopback_up()
    os.execvp(args[0], args)
