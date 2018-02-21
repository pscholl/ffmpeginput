import os, sys, asyncio, itertools, json, socket, time, numpy as np, math
from subprocess import run, PIPE
from threading import Thread
from select import select
from datetime import timedelta

class AttributeDict(dict):
    __getattr__ = dict.__getitem__
    __setattr__ = dict.__setitem__

class Webvtt():
    def __init__(self, f):
        """ read a label from the given file. This call will consume exactly
        one subtitle and fill in the corresponding fields of this object.

         parameters:
          f - file to be read
        """
        beg,end  = f.readline().strip().split(' --> ')
        beg = Webvtt.__timedelta(beg)
        end = Webvtt.__timedelta(end)

        lbl,line = '', f.readline()
        while len(line) > 1:
            lbl += line
            line = f.readline().strip()

        self.beg = timedelta(seconds=beg) if type(beg) != timedelta else beg
        self.end = timedelta(seconds=end) if type(end) != timedelta else end
        self.label = lbl.strip()

        if beg is not None and end is not None:
            self.duration = (self.end - self.beg).total_seconds()
            self.duration = 1 if self.duration == 0 else self.duration


    def __timedelta(string):
        t = [float(x) for x in string.split(':')]
        h,m,s = (0,t[0],t[1]) if len(t)==2 else t
        return timedelta(seconds = h*3600+m*60+s)

    def __timecode(secs):
        try: secs = secs.total_seconds()
        except: pass
        h,m,s = int(secs/3600), int((secs%3600)/60), secs%60
        return '{:02d}:{:02d}:{:06.3f}'.format(h,m,s) if h>0\
          else '{:02d}:{:06.3f}'.format(m,s)

    def __len__(self):
        return math.ceil(self.duration)

    def __repr__(self):
        b,e = Webvtt.__timecode(self.beg), Webvtt.__timecode(self.end)
        return '{} --> {}: {}'.format(b,e,self.label)

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

        audiostreams = [ s for s in streams if s.codec_type == 'audio' ]
        substreams   = [ s for s in streams if s.codec_type == 'subtitle' ]

        if len(streams) == 0:
            raise Exception('no streams selected')

        #
        # create a socket for IPC, which works with select() on Win32 as well.
        # One for writing the file into ffmpeg, one for reading interleaved audio
        # and one for the subtitles.
        #
        r,w,s = self.getsockets(3)

        #
        # build the ffmpeg command line and prepare for reading
        #
        assecond = lambda d: sum( float(t)*f for (t,f) in zip(d.split(':'),[3600,60,1]) )
        duration = min( assecond(s.tags.get('DURATION')) or 0 for s in audiostreams )\
                   if len(audiostreams) > 0 else 0

        #
        # multiple streams can have different sample rates, we use ffmpeg to upsample
        # lower rate streams to simplify further handling of data.
        #
        extra = [extra] * len(streams)\
                if type(extra) == str else extra

        cmd  = 'ffmpeg -loglevel verbose -nostdin '\
                '-t {} -i tcp://localhost:{} '\
                '-max_muxing_queue_size 800000 '\
                '-max_interleave_delta 0 '
        cmd = cmd.format(duration, w.getsockname()[1])

        if len(audiostreams) > 0:
            channels = [ int(s.channels) for s in audiostreams ]
            samplerate = max( int(s.sample_rate) for s in audiostreams )
            self.delta = timedelta(seconds=1./samplerate)
            self.time = timedelta(0)

            cmd += '-ar {} '
            cmd += '-map 0:{} ' * len(audiostreams)
            cmd += '-f f32le tcp://localhost:{} '
            cmd  = cmd.format(samplerate,\
                              *(s.index for s in audiostreams),\
                              r.getsockname()[1])
        else:
            r.close()

        if len(substreams) > 0:
            cmd += '-map 0:{} ' * len(substreams)
            cmd += '-f webvtt tcp://localhost:{} '
            cmd  = cmd.format( *(ss.index for ss in substreams),\
                               s.getsockname()[1])
        else:
            s.close()

        cmd  = cmd.split()

        #
        # start ffmpeg, and wire a socket (so that select works on windows)
        #
        pid = os.fork()
        if pid == 0: # ffmpeg child process
            os.execvp(cmd[0], cmd)

        self.w, _ = w.accept()
        self.thrd = Thread(target=self.__transfer, args=(buf,)).start()

        if len(audiostreams) > 0:
            self.r, _ = r.accept()
            self.buf  = bytearray(sum(channels) * 4)
            self.mem  = memoryview(self.buf).cast('B')
            indices   = list(itertools.accumulate([0] + channels))
            self.view = [ np.array(self.mem[a*4:b*4].cast('f'), copy=False)\
                          for (a,b) in zip(indices, indices[1:]) ]

        if len(substreams) > 0:
            self.s, _ = s.accept()
            self.s = self.s.makefile()

            #
            # read WebVTT header
            #
            a,b = self.s.readline(), self.s.readline()
            if 'WEBVTT' not in a or len(b) != 1:
                raise Exception('not a WebVTT file')

        self.subtitle = None
        self.needsmuxing = len(audiostreams) > 0 and len(substreams) > 0

    def __transfer(self, buf):
        while len(buf) > 0:
            self.w.send(buf)
            buf = self.file.read(SyncReader.BLOCKSIZE)
        self.w.close()

    def __next__(self):
        if not hasattr(self, 'r'): # no audio
            sub = None
            while sub is None:
                try: sub = self.__nextsub()
                except: raise StopIteration
            return sub

        audio = None
        subok = self.subtitle is not None and self.subtitle.end > self.time
        sub   = self.subtitle if subok else None

        try: audio = self.__nextaudio()
        except: self.r.close()

        try: sub = sub or self.__nextsub()
        except: self.s.close()

        self.time += self.delta
        self.subtitle = sub

        if (audio is None and self.subtitle is None) or\
           (audio is None and self.needsmuxing):
            raise StopIteration
        elif self.needsmuxing:
            return audio + [None] if sub is None else\
                   audio + [sub]  if sub.beg <= self.time else\
                   audio + [None]
        else:
            return audio

    def __iter__(self):
        return self

    def __nextsub(self):
        if not hasattr(self, 's'):
            return None

        r,w,e = select([self.s], [], [], .1)
        return None if len(r) == 0 else\
               Webvtt(self.s)

    def __nextaudio(self):
        if not hasattr(self, 'r'):
            return None

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
        #return SyncReader(self.file, self.strms, self.secs, self.extra)
        read = lambda f: SyncReader(f, self.strms, self.secs, self.extra)
        return itertools.chain.from_iterable( read(f) for f in self.files )

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

    subs  = lambda streams: [s for s in streams\
            if s.codec_type == 'subtitle']
    #
    # this prints the first audio streams in 5 block seconds
    #
    for a in input(seconds=5): #, select=subs):
        print(a)
