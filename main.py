#!/usr/bin/env python

"""
Application to pick single-sentence clips from a video
"""

import sys

import gi
gi.require_version('Gst', '1.0')
gi.require_version('GstVideo', '1.0')
gi.require_version('Gtk', '3.0')
gi.require_version('GdkX11', '3.0')
from gi.repository import Gst, GObject, Gtk, GdkX11, GstVideo  # noqa: E402


class Main:
    def __init__(self):
        self.build_ui()
        self.build_gst()

    def build_ui(self):
        self.window = Gtk.Window(Gtk.WindowType.TOPLEVEL)
        self.window.set_title("Video Picker")
        self.window.set_default_size(960, 360)
        self.window.connect('destroy', Gtk.main_quit, 'WM Destroy')

        vbox = Gtk.VBox()
        self.window.add(vbox)

        self.video_window = Gtk.DrawingArea()
        self.video_window.connect('realize', self.on_realize_video_window)
        vbox.add(self.video_window)

        hbox = Gtk.HBox()
        hbox.set_border_width(10)
        vbox.pack_start(hbox, False, False, 0)

        hbox.pack_start(Gtk.Label(), False, False, 0)

        self.open_button = Gtk.Button("Open")
        self.open_button.connect('clicked', self.on_click_open)
        hbox.pack_start(self.open_button, False, False, 0)

        self.play_button = Gtk.Button("Play")
        self.play_button.connect('clicked', self.on_click_play)
        hbox.pack_start(self.play_button, False, False, 0)

        self.exit_button = Gtk.Button("Exit")
        self.exit_button.connect('clicked', self.on_click_exit)
        hbox.pack_start(self.exit_button, False, False, 0)

        hbox.add(Gtk.Label())

        self.window.show_all()

    def build_gst(self):
        self.gst_pipeline = Gst.Pipeline()

        self.gst_playbin = Gst.ElementFactory.make('playbin')
        self.gst_pipeline.add(self.gst_playbin)

        bus = self.gst_pipeline.get_bus()
        bus.add_signal_watch()
        bus.enable_sync_message_emission()
        bus.connect('message', self.on_message)
        bus.connect('sync-message::element', self.on_sync_message)

    def gst_play(self):
        response = self.gst_pipeline.set_state(Gst.State.PLAYING)
        if response == Gst.StateChangeReturn.FAILURE:
            print("ERROR: Could not start playing")

    def on_realize_video_window(self, video_widget):
        window = video_widget.get_property('window')
        self.video_window_xid = window.get_xid()

    def on_click_open(self, widget, data=None):
        dialog = Gtk.FileChooserDialog("Choose a video file", self.window,
                                       Gtk.FileChooserAction.OPEN,
                                       (Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
                                        Gtk.STOCK_OPEN, Gtk.ResponseType.OK))
        response = dialog.run()

        if response == Gtk.ResponseType.OK:
            self.gst_playbin.set_property('uri', 'file://' + dialog.get_filename())
        elif response == Gtk.ReponseType.CANCEL:
            print("Cancelled file dialog")

        dialog.destroy()

    def on_click_play(self, widget, data=None):
        self.gst_play()

    def on_click_exit(self, widget, data=None):
        self.gst_pipeline.set_state(Gst.State.NULL)
        Gtk.main_quit()

    def on_message(self, bus, message):
        t = message.type
        if t == Gst.MessageType.EOS:
            self.gst_pipeline.set_state(Gst.State.NULL)
            self.play_button.set_label("Play")
        elif t == Gst.MessageType.ERROR:
            err, debug = message.parse_error()
            print("Error: " + str(err))
            print(debug)
            self.gst_pipeline.set_state(Gst.State.NULL)
            self.play_button.set_label("Play")

    def on_sync_message(self, bus, message):
        struct = message.get_structure()

        if not struct:
            return

        message_name = struct.get_name()
        if message_name == 'prepare-window-handle':
            message.src.set_property('force-aspect-ratio', True)
            message.src.set_window_handle(self.video_window_xid)


if __name__ == '__main__':
    Gtk.init(sys.argv)
    Gst.init(sys.argv)
    Main()
    GObject.threads_init()
    Gtk.main()
