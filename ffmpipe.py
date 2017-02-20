import sys, os, json
from subprocess import run, PIPE, Popen as popen
from threading import Thread

def os_pipe():
    r,w = os.pipe()
    os.set_inheritable(r, True)
    os.set_inheritable(w, True)
    return r,w

class AttributeDict(dict):
    __getattr__ = dict.__getitem__
    __setattr__ = dict.__setitem__

class FileCopy(Thread):
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
        print("done")

class FFMpegInput():
    def __init__(self, f):
        f = sys.argv[1] if not f else f
        self.f = open(f, 'rb', 0)

        #
        # first probe then read, for this we pipe what
        # we already read to an ffprobe process
        #
        self.probebuf = self.f.read(2048)
        pid = run('ffprobe -loglevel error -show_streams '
                  '-print_format json -'.split(),\
                  input=self.probebuf, stdout=PIPE, timeout=10, check=True)
        streams = json.loads(pid.stdout)['streams']
        self.streams = [AttributeDict(d) for d in streams]

    def __iter__(self):
        #
        # after stream selection, start the ffmpeg instance,
        # push the probe buffer there and switch over to the
        # original file-descriptor as the input for ffmpeg.
        #
        cmd  = 'ffmpeg -loglevel error -nostdin' +\
               ' -i - ' +\
               ' '.join('-map 0:%d ' % x.index for x in self.streams) +\
               ' -f csv -'
        pid = popen(cmd.split(), stdin=PIPE)
        pid.stdin.write(self.probebuf)
        cpy = FileCopy(self.f, pid.stdin)

        return iter([])

def input(f=None, streams=None, blocksize=4096):
    f = FFMpegInput(f)
    c = streams or (lambda s: True)
    f.streams = [ s for s in f.streams if c(s) ]
    return f

if __name__ == '__main__':
    gotya = lambda s: s.codec_type=='audio' and\
                      s.sample_rate=='40'

    for s in input(streams=gotya):
        print('.')

    #for (s,a,b) in input(streams=gotya, blocksize=4096):
    #    pass
