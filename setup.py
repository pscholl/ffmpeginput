#!/usr/bin/env python

import os, sys
from distutils.core import setup

if sys.argv[1] == 'test':
    p = os.system('python -m doctest README.md')

else:
    setup( name = 'ffmpeginput',
           version = '1.0',
           description = 'wrapper for ffmpeg/ffprobe for unix-like pipe I/O',
           author = 'Philipp M. Scholl',
           author_email = 'pscholl@ese.uni-freiburg.de',
           py_modules = ['ffmpeginput']
          )
