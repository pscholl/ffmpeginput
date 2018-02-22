# ffmpeginput - python module for using ffmpeg as a pipe

 This module allows to write [Unix filters][1], to be used in pipes, with ffmpeg
as the input parsers. Pretty much in the same way the [fileinput][2] allows you
to do for text files. Why? You can now use the capabilities of ffmpeg to
transport structured data, or simply parse multi-media files in your scripts.
For example, the following snippets loads all audio and subtitle streams from an
input file file into memory (video is currently not supported):

You can load the whole file into memory by supplying the read parameter:

    >>> from ffmpeginput import input

    >>> a,*_ = input('example.mkv', read=True)
    >>> a.shape
    (1000,)

 which loads the first audio stream. You can also go more efficient by reading
the file on sample-per-sample basis:

    >>> from ffmpeginput import input

    >>> for a,*_,m in input('example.mkv'):
    ...   a, m.samplerate
    ...   break
    (array([0.], dtype=float32), 40)

 this is the first sample of the first stream in the file, and accessing the
samplerate of the output.

 You can also limit the streams that should be read from the file with a
selector function:

       >>> from ffmpeginput import input
       >>> audio = lambda streams: [s for s in streams if s.codec_type == 'audio']
       >>> subtitle = lambda streams: [s for s in streams if s.codec_type == 'subtitle']
       >>> strms = lambda streams: audio(streams)[:1] + subtitle(streams) 
       >>> a,s,m = input('example.mkv', select=strms, read=True)
       >>> m[0].samplerate # this contains meta-information, like the samplerate
       40

       >>> a.shape         # this is the first audio-stream
       (1000,)

       >>> s[100]          # and this contains the interleaved subtitles
       00:00.000 --> 00:04.000: just some marker

 This selector function selects the first audio stream and all subtitle streams in the
input file.

 Only calling '''input()''' will read files from the standard input, or the files given
as arguments to the scripts (if any).

## Installation

 Install with your local python installation with:
 
  python setup.py install --prefix=/usr

## Requirements

 Requires a recent version of the ffmpeg binary.

[1]: https://www.bell-labs.com/usr/dmr/www/hist.html#pipes
[2]: https://docs.python.org/3/library/fileinput.html
[3]: https://www.numpy.org
