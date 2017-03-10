#!/usr/bin/env python

from distutils.core import setup

setup( name = 'ffmpeginput',
       version = '1.0',
       description = 'wrapper for ffmpeg/ffprobe for unix-like pipe I/O',

       author = 'Philipp M. Scholl',
       author_email = 'pscholl@ese.uni-freiburg.de',

       py_modules = ['ffmpeginput']
      )
