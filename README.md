# ffmpeginput - python module for using ffmpeg as a pipe

 This module allows to write [Unix filter][1], to be used in pipes, with ffmpeg
as the input parsers. Pretty much in the same way the [fileinput][2] allows you
to do for text files. For example the following snippet will read from stdin, or
from the filenames supplied to the script and supply [numpy][3] arrays for the
selected audio streams for every selected epoch:

    >>> from ffmpeginput import input

    >>> audio = lambda stream,all: stream.codec_type == 'audio'
    >>> for (a,*_) in input(select=audio, seconds=5):
    ...     print(a.shape)
    
 or you can load the whole input into memory at once:
    >>> from ffmpeginput import input

    >>> audio = lambda stream,all: stream.codec_type == 'audio'
    >>> a,*_ = input(select=audio)
    >>> print(a.shape)

## Installation

 Install with your local python installation with:
 
     >>> python setup.py install --prefix=/usr
     
## Requirements

 Requires a recent version of the ffmpeg binary.

[1]: https://www.bell-labs.com/usr/dmr/www/hist.html#pipes
[2]: https://docs.python.org/3/library/fileinput.html
[3]: https://www.numpy.org
