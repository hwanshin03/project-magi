"""Process-wide restrictions for this synthetic-only local preview."""
import os
from pathlib import Path
import socket
import sys


def audit(event, args):
    if event in {'socket.connect', 'socket.getaddrinfo', 'socket.gethostbyname', 'socket.gethostbyaddr',
                 'sqlite3.connect', 'subprocess.Popen', 'os.system'}:
        raise PermissionError('Preview forbids outbound connections, databases and subprocesses')
    if event == 'socket.bind' and args[1][0] != '127.0.0.1':
        raise PermissionError('Preview requires IPv4 loopback')
    if event == 'open' and isinstance(args[0], (str, bytes, os.PathLike)):
        path = Path(os.fsdecode(args[0]))
        if (path.name.startswith('.env') or path.suffix in {'.db', '.sqlite', '.sqlite3'}
                or any(p in {'.aws', '.ssh', '.gnupg', 'MAGI-private'} for p in path.parts)):
            raise PermissionError('Preview forbids credential and private database files')
    if event == 'import' and (args[0].startswith('magi.broker')
                             or args[0] in {'magi.portfolio', 'magi.memory'}):
        raise PermissionError('Preview forbids broker and portfolio initialization')


def install():
    # Do not inherit API configuration into a synthetic-report process.
    os.environ.clear()
    os.environ['PYTHON_DOTENV_DISABLED'] = '1'
    sys.dont_write_bytecode = True
    # Avoid a dependency's IPv6 availability probe; only IPv4 loopback is allowed.
    socket.has_ipv6 = False
    sys.addaudithook(audit)
