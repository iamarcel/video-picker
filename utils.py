#!/usr/bin/env python

"""
Utility functions for video-picker
"""

import gi
gi.require_version('Gst', '1.0')
from gi.repository import Gst  # noqa: E402


def try_link(src_pad, dst_pad):
    print("Linking " + str(src_pad) + " with " + str(dst_pad))
    result = src_pad.link(dst_pad)
    if result != Gst.PadLinkReturn.OK:
        raise Exception("Failed to link " + str(src_pad)
                        + " with " + str(dst_pad) + "; got state "
                        + str(result))
