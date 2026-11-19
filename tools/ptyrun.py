#!/usr/bin/python3
"""Test helper: run a TUI (e.g. the official localsend-cli) in a pty and script it.

usage: ptyrun.py LOG -- CMD... ;; STEP...
STEP: WAIT:<s>, PICK:<device name>, KEY:<text> (\r \x1b escapes ok), SH:<shell command, run and waited for>.
Writes everything the program printed, ANSI escapes stripped, to LOG.
"""
import fcntl, os, pty, re, select, struct, subprocess, sys, termios, time

log = sys.argv[1]
rest = sys.argv[3:]
cut = rest.index(';;')
cmd, steps = rest[:cut], rest[cut + 1:]
pid, fd = pty.fork()
if pid == 0:
    os.environ['TERM'] = 'xterm-256color'
    os.execvp(cmd[0], cmd)
fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack('HHHH', 30, 120, 0, 0))
buf = b''


def pump(seconds):
    global buf
    end = time.time() + seconds
    while time.time() < end:
        r, _, _ = select.select([fd], [], [], 0.05)
        if r:
            try:
                buf += os.read(fd, 65536)
            except OSError:
                return


for step in steps:
    kind, _, arg = step.partition(':')
    if kind == 'WAIT':
        pump(float(arg))
    elif kind == 'KEY':
        os.write(fd, arg.encode().decode('unicode_escape').encode('latin-1'))
        pump(0.3)
    elif kind == 'PICK':   # press the number shown as "[n] <arg>" in the device list
        plain = re.sub(rb'\[[0-9;?]*[A-Za-z]', b' ', buf).decode('utf-8', 'replace')
        found = re.findall(r'\[(\d)\] ' + re.escape(arg), plain)
        print('PICK', arg, found, flush=True)
        if found:
            os.write(fd, found[-1].encode())
        pump(0.3)
    elif kind == 'SH':
        p = subprocess.Popen(arg, shell=True)
        while p.poll() is None:
            pump(0.2)
pump(0.5)
try:
    os.kill(pid, 9)
except OSError:
    pass
text = re.sub(rb'\x1b\[[0-9;?]*[A-Za-z]|\x1b[()][A-Z0-9]|\x1b[=>]', b' ', buf).decode('utf-8', 'replace')
text = re.sub(r' {2,}', '  ', text)
open(log, 'w').write(text)
