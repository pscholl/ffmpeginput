# ffmpeginput - python module for using ffmpeg as a pipe

 This module allows to write [Unix filters][1], to be used in pipes, with ffmpeg
as the input parsers. Pretty much in the same way the [fileinput][2] allows you
to do for text files. Why? You can now use the capabilities of ffmpeg to
transport structured data, or simply parse multi-media files in your scripts.
For example, the following snippets loads all audio and subtitle streams from an
input file file into memory:

    >>> from ffmpeginput import input
    >>> strms = lambda stream,all: stream.codec_type == 'audio' or\
    ...                            stream.codec_type == 'subtitle'
    >>> a,b,c,s = input('example.mkv', select=strms)


## Installation and Requirements

## Installation
## Requirements

[1]: https://www.bell-labs.com/usr/dmr/www/hist.html#pipes
[2]: https://docs.python.org/3/library/fileinput.html
[3]: https://www.numpy.org
