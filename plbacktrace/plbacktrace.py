#!/usr/bin/env python
# ----------------------------------------------------------------------
#
#   plbacktrace.py
#
#   A utility to display the PL/pgSQL call levels of a running
#   PostgreSQL backend with their function OID, signature and
#   current line number.
#
#   usage: plbacktrace.py PID
#
#   Copyright (c) 2016 Jan Wieck
#
#   License: PostgreSQL - https://www.postgresql.org/about/licence/
#
# ----------------------------------------------------------------------

import Queue
import re
import subprocess
import sys
import threading

def main():
    if len(sys.argv) != 2:
        sys.stderr.write("usage: plstack.py BACKENDPID\n")
        return 2

    # ----
    # Our only command line argument is the backend PID.
    # ----
    be_pid = int(sys.argv[1])

    # ----
    # We need some regular expressions below. For efficiency, we
    # compile them.
    # ----
    rex1 = re.compile("#([0-9]+) +0x[0-9a-f]+ in ([^ ]+) ")
    rex2 = re.compile("#([0-9]+) +([^ ]+) ")
    rex3 = re.compile("\\$[0-9]+ = (.*)")

    # ----
    # Start gdb. This assumes that the relevant 'postgres' executable
    # is found via $PATH.
    # ----
    gdb = subprocess.Popen(['gdb', '-se', 'postgres'],
                           stdin = subprocess.PIPE,
                           stdout = subprocess.PIPE,
                           stderr = subprocess.PIPE);
    
    # ----
    # To avoid possible deadlocks between input and output with the
    # gdb subprocess, we use threads to read the stdout and stderr
    # and a Queue to forward that data to the main process.
    # ----
    gdbq = Queue.Queue()

    gdbrdr = gdb_reader(gdb.stdout, gdbq)
    gdbrdr.start()

    gdberr = gdb_stderr(gdb.stderr, gdbq)
    gdberr.start()

    # ----
    # Time to attach gdb to the backend and get a stack backtrace.
    # ----
    gdb.stdin.write("attach %d\n" %be_pid)
    gdb.stdin.write("bt\n")
    gdb.stdin.flush()

    # ----
    # From here on we are a state machine. We process the gdb output
    # and respond to certain patterns with additional commands or
    # collecting and printing information.
    # ----
    pl_frame = []
    need_lno = True
    while True:
        # ----
        # Get the next line from gdb's stdout. We are done if gdb
        # closed that.
        # ----
        line = gdbq.get()
        if line is None:
            break

        # ----
        # Remove all leading (gdb) prompts.
        # ----
        while line[0:5] == '(gdb)':
            line = line[5:].strip()
        line = line.strip()

        # ----
        # The first two regular expressions (rex1 and rex2) recognize
        # stack frame lines from the "bt" command. Those look like
        #
        #       #NN FUNCNAME (...
        #       #NN 0xHEXADDR in FUNCNAME (...
        #
        # NN is the stack frame and FUNCNAME is the C language function
        # name at that frame.
        # ----
        m = rex1.match(line)
        if m is None:
            m = rex2.match(line)
        if m is None:
            # ----
            # If it is not a stack frame, then it might be the output
            # from one of the "print" commands, issued below. For each
            # PL/pgSQL call level, the code below will issue three
            # print commands. The first is on the first encounter of a
            # exec_stmt() function. Here we print the lineno of the stmt.
            # The next two are the fn_oid and fn_signature fields of the
            # func pointer when we find the plpgsql_exec_function() or
            # plpgsql_exec_trigger() call. The output of those "print"
            # commands is a line like
            #
            #       $NN = VALUE
            #
            # Whenever we collected three values, we have a PL/pgSQL
            # stack frame.
            # ----
            m = rex3.match(line)
            if m is not None:
                pl_frame.append(m.groups()[0])
                if len(pl_frame) == 3:
                    fn_signature = pl_frame[2][pl_frame[2].find(' ') + 1:]
                    print "fn_oid=%s lineno=%s func=%s" %(
                        pl_frame[1], pl_frame[0], fn_signature,)
                    pl_frame = []
            continue
                
        # ----
        # We detected a stack frame line from the initial "bt" command.
        # ----
        if m.groups()[1] == 'exec_stmt':
            # ----
            # If this is the first exec_stmt (after a function/trigger),
            # print the statements lineno.
            # ----
            if need_lno:
                gdb.stdin.write('select-frame %s\n' %m.groups()[0])
                gdb.stdin.write('p stmt->lineno\n')
                need_lno = False

        elif m.groups()[1] in ('plpgsql_exec_function', 'plpgsql_exec_trigger', ) :
            # ----
            # If this is a function or trigger call, print the pg_proc
            # oid and the function/trigger call signature. Then wait for
            # the next exec_stmt to record a lineno.
            # ----
            gdb.stdin.write('select-frame %s\n' %m.groups()[0])
            gdb.stdin.write('l\n')
            gdb.stdin.write('p func->fn_oid\n')
            gdb.stdin.write('p func->fn_signature\n')
            need_lno = True

        elif m.groups()[1] == 'main':
            # ----
            # Once we hit main(), we have queued all the gdb commands to
            # get the information needed. We can therefore queue a "quit"
            # command so that gdb will exit.
            # ----
            gdb.stdin.write('quit\n')
        
        # ----
        # Make sure that gdb gets the message(s).
        # ----
        gdb.stdin.flush()


# ----
# gdb_stderr
#
#   This is a small helper class that will forward gdb's stderr to
#   the real stderr and send a None to the queue to signal the main
#   thread to abort.
# ----
class gdb_stderr(threading.Thread):
    def __init__(self, fd_err, queue):
        threading.Thread.__init__(self)

        self.fd_err = fd_err
        self.queue = queue
        self.daemon = True

    def run(self):
        line = self.fd_err.readline()
        while line:
            sys.stderr.write(line)
            sys.stderr.flush()
            line = self.fd_err.readline()
            self.queue.put(None)


# ----
# gdb_reader
#
#   This is a helper thread that feeds gdb's stdout into a queue
#   for the main thread. It signals EOF by sending None into the
#   queue.
# ----
class gdb_reader(threading.Thread):
    def __init__(self, fd_in, queue):
        threading.Thread.__init__(self)

        self.fd_in = fd_in
        self.queue = queue
        self.daemon = True

    def run(self):
        line = self.fd_in.readline()
        while line:
            self.queue.put(line)
            line = self.fd_in.readline()

        self.queue.put(None)


if __name__ == '__main__':
    sys.exit(main())
