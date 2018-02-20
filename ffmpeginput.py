import os, sys, asyncio, itertools, json, socket, time, numpy as np
from subprocess import run, PIPE
from threading import Thread

class AttributeDict(dict):
    __getattr__ = dict.__getitem__
    __setattr__ = dict.__setitem__


class SyncReader():

    BLOCKSIZE = 8192 * 64

    def getsockets(self, num):
        socks = [ socket.socket(socket.AF_INET, socket.SOCK_STREAM)\
                  for i in range(num) ]
        for s in socks:
            s.bind(('', 0))
            s.listen(1)
        return socks

    def __init__(self, file, strms, seconds, extra):
        self.file = open(file, 'rb') if file != '-' else\
                    os.fdopen(sys.stdin.fileno(), 'rb')
        buf = self.file.read(SyncReader.BLOCKSIZE)

        #
        # probe the input file and select streams
        #
        ffprobe   = run('ffprobe -loglevel error -show_streams '
                        '-print_format json -'.split(),
                        input   = buf,
                        stdout  = PIPE,
                        timeout = 10,
                        check   = True)

        streams   = json.loads(ffprobe.stdout.decode('utf-8-sig'))['streams']
        streams   = strms([AttributeDict(d) for d in streams])

        if len(streams) == 0:
            raise Exception('no streams selected')

        #
        # create a socket for IPC, which works with select() on Win32 as well
        #
        r,w = self.getsockets(2)

        #
        # build the ffmpeg command line and prepare for reading
        #
        assecond = lambda d: sum( float(t)*f for (t,f) in zip(d.split(':'),[3600,60,1]) )
        duration = min( assecond(s.tags.get('DURATION')) or 0 for s in streams )

        #
        # multiple streams can have different sample rates, we use ffmpeg to upsample
        # lower rate streams to simplify further handling of data.
        #
        samplerate = max( int(s.sample_rate) for s in streams )
        channels = [ int(s.channels) for s in streams ]
        extra = [extra] * len(streams)\
                if type(extra) == str else extra

        cmd  = 'ffmpeg -loglevel error -nostdin '\
                '-t {} -i tcp://localhost:{} '\
                '-max_muxing_queue_size 800000 '\
                '-ar {} '\
                '-max_interleave_delta 0 '
        cmd += '-map 0:{} ' * len(streams)
        cmd += '-f f32le tcp://localhost:{}'
        cmd  = cmd.format(duration,\
                          w.getsockname()[1],\
                          samplerate,\
                          *(s.index for s in streams),\
                          r.getsockname()[1])
        cmd  = cmd.split()

        #
        # start ffmpeg, and wire a socket (so that select works on windows)
        #
        pid = os.fork()
        if pid == 0: # ffmpeg child process
            os.execvp(cmd[0], cmd)
        else:
            self.w, _ = w.accept()
            self.thrd = Thread(target=self.__transfer, args=(buf,)).start()
            self.r, _ = r.accept()
            self.buf  = bytearray(len(streams) * sum(channels) * 4)
            self.mem  = memoryview(self.buf).cast('B')
            indices   = list(itertools.accumulate([0] + channels))
            self.view = [ np.array(self.mem[a*4:b*4].cast('f'), copy=False)\
                          for (a,b) in zip(indices, indices[1:]) ]

    def __transfer(self, buf):
        while len(buf) > 0:
            self.w.send(buf)
            buf = self.file.read(SyncReader.BLOCKSIZE)
        self.w.close()
        self.stop()

    def __next__(self):
        mem, len = self.mem, 0

        while len < mem.nbytes:
            n = self.r.recv_into(mem[len:], mem.nbytes - len)

            if n == 0:
                raise StopIteration

            len += n

        return self.view


class FFmpegInput():

    def __init__(self, files=None, select=None, seconds=None, extra=''):
        """
        open ffmpeg-readable files and return an iterator, which loops
        over blocks of data from each stream in an interleaved fashion.
        Streams can be selected by providing a function in the _select_
        parameter, which gets called with a list of streams, and needs
        to return list of selected streams.

        parameters:
         files   - (optional) list of or single path(s) to open, if not 
                   given read sys.argv or sys.stdin
         select  - (optional) callable to select the streams that are to
                   be read, *select(s,streams)* will be multiple times for
                   each stream in streams and with the overall list, it's
                   return value decides whether the stream is included or
                   not.
         seconds - (optional) blocksize in seconds, if not specified returns
                   the whole file
        """
        self.files = files or \
                     sys.argv[1:] if len(sys.argv[1:]) \
                     else ['-'] # for stdin
        self.strms = select or (lambda s: s)
        self.secs  = seconds
        self.extra = extra

    #
    #
    # Asynchronous API support
    #
    def __aiter__(self):
        pass

    def __anext__(self):
        pass

    #
    # Synchronous API support
    #
    def __iter__(self):
        return SyncReader(self.files[0], self.strms, self.secs, self.extra)
        #return itertools.chain( *(\
        #        SyncReader(f, self.strms, self.secs, self.extra)\
        #        for f in self.files) )

#
# export the class directly as a function
#
input = FFmpegInput

if __name__ == '__main__':
    #
    # selector for audio streams only
    #
    audio = lambda streams: [s for s in streams\
            if s.codec_type == 'audio']
    #
    # this prints the first audio streams in 5 block seconds
    #
    for a in input(seconds=5, select=audio):
        print(a)
