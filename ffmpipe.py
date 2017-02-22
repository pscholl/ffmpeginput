import sys, os, json, itertools
from subprocess import run, PIPE, Popen as popen
from threading import Thread
from datetime import timedelta
from select import select
from math import ceil

def os_pipe():
    r,w = os.pipe()
    os.set_inheritable(r, True)
    os.set_inheritable(w, True)
    return r,w

class AttributeDict(dict):
    __getattr__ = dict.__getitem__
    __setattr__ = dict.__setitem__

class CopyFile(Thread):
    """
    A Thread to copy data blockwise from one file-descriptor to the next one, this
    is needed by the FFMpegInput class, as we need tee-like behaviour to probe 
    before decoding the data in the input.
    """
    def __init__(self, r,w,bufsize=4096):
        Thread.__init__(self)
        self.bufsize = bufsize
        self.r = r
        self.w = w
        self.start()

    def run(self):
        buf = self.r.read(self.bufsize)
        while len(buf) > 0:
            self.w.write(buf)
            buf = self.r.read(self.bufsize)

class FFMpegInput():
    def __init__(self, path, streamselect):
        try: self.f = open(path, 'rb', 0)
        except: self.f = path

        #
        # first probe then read, for this we pipe what
        # we already read to an ffprobe process
        #
        self.probebuf = self.f.read(2048 * 16)
        pid = run('ffprobe -loglevel error -show_streams '
                  '-print_format json -'.split(),\
                  input=self.probebuf, stdout=PIPE, timeout=10, check=True)
        streams = json.loads(pid.stdout)['streams']
        self.streams = [AttributeDict(d) for d in streams]
        self.streams = [d for d in self.streams if streamselect(d)]

    def __iter__(self, seconds=5):
        """
        after stream selection, start the ffmpeg instance,
        push the probe buffer there and switch over to the
        original file-descriptor as the input for ffmpeg.

        The selected streams are demuxed to several files,
        for each of which we create a pipe and hence need
        to use os.fork etc.

        Parameters:
         seconds - duration of packets that are to be read
        """

        output = {
         'audio'    : ' -map 0:{s[index]} -f f32le pipe:{p[1]} ',
         'subtitle' : ' -map 0:{s[index]} -f srt pipe:{p[1]} ' }

        self.pipes = [ os_pipe() for s in self.streams ]
        stdin,stdout = os_pipe()

        cmd = 'ffmpeg -loglevel error -nostdin' +\
               ' -i pipe:%d ' % stdin +\
               ' '.join(output[s.codec_type].format(s=s, p=p) \
                   for (s,p) in zip(self.streams,self.pipes))

        cmd = cmd.split()
        pid = os.fork()

        if pid == 0: # ffmpeg child
            [ os.close(r) for (r,_) in self.pipes ]
            os.close(stdout)
            os.execvp(cmd[0], cmd)

        [ os.close(w) for (_,w) in self.pipes ]
        os.close(stdin)

        stdout = os.fdopen(stdout, 'wb', buffering=0)
        stdout.write(self.probebuf)
        CopyFile(self.f, stdout)

        return InterleavedPipesIterator(self, seconds)

class SubripReader():
    """
    special file reader that checks whether a call to read would block,
    if so returns None.
    """
    def __init__(self, f):
        self.f = f
        self.fileno = f.fileno

    class Label():
        def __init__(self, f):
            try:
                self.no = f.readline().strip()
                self.beg,self.end = f.readline().strip().split(' --> ')
                self.label = f.readline()

                while len(f.readline()) > 1:
                    label += f.readline()

                self.beg = SubripReader.Label.__timedelta(self.beg)
                self.end = SubripReader.Label.__timedelta(self.end)
                self.duration = (self.end - self.beg).total_seconds()
                self.duration = 1 if self.duration == 0 else self.duration
            except:
                self.duration = 0

        def __timedelta(s):
            h,m,s = (float(x.replace(',','.')) for x in s.split(':'))
            return timedelta(seconds = h*3600+m*60+s)

        def __len__(self):
            return ceil(self.duration)

        def __repr__(self):
            return '{}\n{} --> {}\n{}\n'.format( self.no,self.beg,self.end,self.label )

    def read(self):
        return SubripReader.Label(self.f)


class InterleavedPipesIterator():
    """
    Iterates through a list of ffmpeg streams in a synchronous fashion, keeping
    track of the current time in the streams and making sure that the reading
    process will not be blocked.
    """
    def __init__(self, ffmpeg, duration):
        """
        prepare the iteration

        Parameters:
         ffmpeg - an FFMpegInput object to iterate through
         duration - duration of the packets to be read
        """
        self.s = ffmpeg.streams
        self.p = (r for (r,_) in ffmpeg.pipes)
        self.p = [os.fdopen(r, 'rb' if s.codec_type == 'audio' else 'r')\
                  for (r,s) in zip(self.p, self.s)]
        self.p = [SubripReader(f) if s.codec_type == 'subtitle' else f\
                  for (f,s) in zip(self.p, self.s)]
        self.d = duration
        self.frameno = 0

        if any(s.codec_type == 'video' for s in self.s):
            raise NotImplemented('reading video data is not implemented')

    def __iter__(self):
        return self

    def __next__(self):
        """
        video and audio streams can be read in a synchronuous fashion,
        while subtitle streams need to be read in a non-blocking fashion.

        We do so by wrapping the subtitle streams with a special select()
        based reader.
        """
        aud = lambda f,s: f.read(int(float(s.sample_rate) * 4 * self.d))
        sub = lambda f,s: f.read()
        fin = lambda b: (not b is None) and len(b)==0
        read = lambda p,s: aud(p,s) if s.codec_type == 'audio' else sub(p,s)

        rdy,*_ = select(self.p,[],[])
        blk = [ read(p,s) if p in rdy else None\
                for (p,s) in zip(self.p, self.s) ]

        if all(fin(b) for b in blk):
            raise StopIteration()

        #
        # replace finished streams with None
        #
        return [ None if fin(b) else b for b in blk ]


def input(files=None, select=None, seconds=5):
    """
    open an ffmpeg readable file and return an iterator, which loops
    over blocks of data from each stream in an interleaved fashion.

    parameters:
     files   - (optional) list of or single path(s) to open, if not
               given read sys.argv or sys.stdin
     select  - (optional) callable to select the streams that are
               to be read
     seconds - (optional) blocksize to be read, defaults to 5 secs
    """
    files = files or \
            sys.argv[1:] if len(sys.argv[1:]) \
            else os.fdopen(sys.stdin.fileno(), 'rb')
    strms = select or (lambda s: True)
    _iter = lambda f: FFMpegInput(f,strms).__iter__(seconds)

    #
    # concatenate all input files, so that all blocks from file1
    # are yielded, then all block from file2 etc.
    #
    return itertools.chain( *(_iter(f) for f in files) )

if __name__ == '__main__':
    gotya = lambda s: s.codec_type=='audio' and\
                      s.sample_rate=='40'
    subs = lambda s: s.codec_type == 'subtitle'

    for (s,*_) in input(seconds=5, select=subs):
        if s is not None:
            print(s)
