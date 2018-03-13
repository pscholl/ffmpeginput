# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
# Author: Philipp M. Scholl <pscholl@bawue.de>

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

    def __init__(self, file, strms, extra):
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
        # keep track of all information about the processed streams
        #
        class empty(object): pass
        meta = empty()

        #
        # build the ffmpeg command line and prepare for reading
        #
        assecond = lambda d: sum( float(t)*f for (t,f) in zip(d.split(':'),[3600,60,1]) )
        duration = min( assecond(s.tags.get('DURATION')) or 0 for s in audiostreams )\
                   if len(audiostreams) > 0 else 0
        meta.duration = duration
        self.duration = duration

        #
        # multiple streams can have different sample rates, we use ffmpeg to upsample
        # lower rate streams to simplify further handling of data.
        #
        extra = [extra] * len(streams)\
                if type(extra) == str else extra

        cmd  = 'ffmpeg -loglevel quiet -nostdin '\
                '-t {} -i tcp://localhost:{} '\
                '-max_muxing_queue_size 800000 '\
                '-max_interleave_delta 0 '
        cmd = cmd.format(duration, w.getsockname()[1])

        if len(audiostreams) > 0:
            self.channels = [ int(s.channels) for s in audiostreams ]
            self.samplerate = max( int(s.sample_rate) for s in audiostreams )
            self.delta = timedelta(seconds=1./self.samplerate)
            self.time = timedelta(0)

            cmd += '-ar {} '
            cmd += '-map 0:{} ' * len(audiostreams)
            cmd += '-f f32le tcp://localhost:{} '
            cmd  = cmd.format(self.samplerate,\
                              *(s.index for s in audiostreams),\
                              r.getsockname()[1])
            #
            # Build an info object that is passed through
            #
            meta.samplerate = self.samplerate
            meta.audiostreams = audiostreams

        else:
            r.close()

        if len(substreams) > 0:
            cmd += '-map 0:{} ' * len(substreams)
            cmd += '-f webvtt tcp://localhost:{} '
            cmd  = cmd.format( *(ss.index for ss in substreams),\
                               s.getsockname()[1])

            meta.substreams = substreams
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
        self.meta = meta

        #
        # prepare the input bufffers for reading audio data
        #
        if len(audiostreams) > 0:
            self.r, _ = r.accept()

        #
        # prepare for reading subtitles
        #
        if len(substreams) > 0:
            self.s, _ = s.accept()
            self.s = self.s.makefile()
            self.gotheader = False

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
            ret = audio + [None] if sub is None else\
                  audio + [sub]  if sub.beg <= self.time else\
                  audio + [None]

        else:
            ret = audio

        return ret + [self.meta]

    def __iter__(self):
        #
        # prepare a buffer for iteration
        #
        self.buf  = bytearray(sum(self.channels) * 4)
        self.mem  = memoryview(self.buf).cast('B')
        indices   = list(itertools.accumulate([0] + self.channels))
        self.view = [ np.array(self.mem[a*4:b*4].cast('f'))\
                      for (a,b) in zip(indices, indices[1:]) ]
        return self

    def __nextsub(self):
        if not hasattr(self, 's'):
            return None

        r,w,e = select([self.s], [], [], .1)

        if len(r) == 0:
            return None

        if self.gotheader == 0:
            a = self.s.readline()
            self.gotheader = 1
            if 'WEBVTT' not in a:
                raise Exception('not a WebVTT file')
        elif self.gotheader == 1:
            b = self.s.readline()
            self.gotheader = 2
            if len(b) != 1:
                raise Exception('not a WebVTT file')
        else:
            return Webvtt(self.s)

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

    def read(self):
        """ read all files in one go
        """
        buf = bytearray(sum(self.channels) * self.samplerate * math.ceil(self.duration) * 4)
        mem,len = memoryview(buf).cast('B'), 0
        read = []
        read = read + [self.s] if hasattr(self, 's') else read
        read = read + [self.r] if hasattr(self, 'r') else read
        subs = []

        while read.__len__() > 0:
            r,w,e = select(read, [], [])

            if hasattr(self, 's') and self.s in r:
                try:
                    sub = self.__nextsub()
                    if sub:
                        subs.append(sub)
                except: 
                    read.remove(self.s)

            if hasattr(self, 'r') and self.r in r:
                n = self.r.recv_into(mem[len:], mem.nbytes - len)
                if n == 0:
                    read.remove(self.r)
                len += n

        #
        # resample the subtitle to fit the samplerate
        #
        tosmplr = lambda secs: int(secs * self.samplerate)
        newsubs = [None] * tosmplr(self.duration)
        for sub in subs:
            beg = tosmplr(sub.beg.total_seconds())
            end = tosmplr(sub.end.total_seconds())
            newsubs[beg:end] = [sub] * (end-beg)

        #
        # create a numpy array from the memoryview
        #
        mem = mem.cast('f')
        num = sum(self.channels)
        idx = list(itertools.accumulate([0] + self.channels))
        arr = np.array(mem, copy=False).reshape((-1,num))
        arr = [ arr[:, a:b] for (a,b) in zip(idx,idx[1:]) ]
        return arr + [newsubs] + [self.meta]


class FFmpegInput():

    def __init__(self, files=None, select=None, extra=''):
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
        """
        self.files = [ files ]    if type(files) == str else\
                     files        if files is not None  else\
                     sys.argv[1:] if len(sys.argv[1:])  else\
                     ['-']        # for stdin
        self.strms = select or (lambda s: s)
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
        readers = [SyncReader(f, self.strms, self.extra) for f in self.files]
        return itertools.chain.from_iterable( read for read in readers )

    def read(self):
        readers = [SyncReader(f, self.strms, self.extra) for f in self.files]
        return [reader.read() for reader in readers]

#
# export the class directly as a function
#
def input(files=None, select=None, extra='', read=False):
    ffinput = FFmpegInput(files, select, extra)

    if not read:
        return ffinput
    else:
        data = ffinput.read()
        return data[0] if len(data)==1 else data


if __name__ == '__main__':
    #
    # selector for audio streams only
    #
    audio = lambda streams: [s for s in streams\
            if s.codec_type == 'audio'][8:9]

    subs  = lambda streams: [s for s in streams\
            if s.codec_type == 'subtitle']

    a,b,c,*_ = input(sys.argv[1], read=True, select=audio)
    print(a)
    sys.exit(-1)

    #
    # just print infos about the selected streams
    #
    for *_, m in input(sys.argv[1], select=lambda s: audio(s)+subs(s)):
        for a in m.audiostreams:
            s = "input {}: {} {} {}".format(\
                    a.index, a.sample_fmt, a.sample_rate,\
                    a.tags['DURATION'])
            print(s)

        for a in m.substreams:
            s = "input {}: {} {}".format(\
                    a.index, a.codec_type, a.tags['DURATION'])
            print(s)

        print("output at rate {} for {} seconds".format(\
                m.samplerate, m.duration))
        sys.exit(-1)
