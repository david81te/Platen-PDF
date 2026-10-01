"""Platen PDF backend.

The one thing that happens at import time is pinning WebView2's colour
handling, because it has to be in place before the first window is created and
every entry point - the application and every UI test - imports this package
before it opens one.
"""
import os

# Chromium colour-manages its content to the display's ICC profile. On a
# machine with a warm or wide-gamut monitor profile that turns white into
# something that is not white: a plain white PDF page, rendered as 255,255,255
# and verified as 255,255,255 inside the DOM, reached the screen as
# 255,252,222 - a visible cream - and the application's own dark grey arrived
# eight points off the value in the stylesheet. Neither the page nor the
# renderer was wrong; both measured exactly correct. The conversion happens
# after the pixels leave Python, so nothing on this side can undo it.
#
# A PDF editor should show the page the way the file describes it, and should
# look the same on every machine, so the content is pinned to sRGB. Left
# overridable for anyone who genuinely wants their display profile honoured.
os.environ.setdefault("WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS",
                      "--force-color-profile=srgb")
