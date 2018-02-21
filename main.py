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
from gi.repository import Gst, GObject, Gtk, GdkX11, GstVideo, GLib  # noqa: E402


class Main:
    def __init__(self):
        self.build_ui()
        self.build_gst()

    def build_ui(self):
        self.window = Gtk.Window(Gtk.WindowType.TOPLEVEL)
        self.window.set_title("Video Picker")
        self.window.set_default_size(960, 640)
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

        self.open_button = (
            Gtk.Button(image=Gtk.Image.new_from_stock(Gtk.STOCK_OPEN,
                                                      Gtk.IconSize.BUTTON)))
        self.open_button.connect('clicked', self.on_click_open)
        hbox.pack_start(self.open_button, False, False, 2)

        self.play_button = (
            Gtk.Button(image=Gtk.Image.new_from_stock(Gtk.STOCK_MEDIA_PLAY,
                                                      Gtk.IconSize.BUTTON)))
        self.play_button.connect('clicked', self.on_click_play)
        hbox.pack_start(self.play_button, False, False, 2)

        self.pause_button = (
            Gtk.Button(image=Gtk.Image.new_from_stock(Gtk.STOCK_MEDIA_PAUSE,
                                                      Gtk.IconSize.BUTTON)))
        self.pause_button.connect('clicked', self.on_click_pause)
        hbox.pack_start(self.pause_button, False, False, 2)

        self.exit_button = Gtk.Button("Exit")
        self.exit_button.connect('clicked', self.on_click_exit)
        hbox.pack_start(self.exit_button, False, False, 2)

        self.slider = Gtk.HScale.new_with_range(0, 100, 0.5)
        self.slider.set_draw_value(False)
        self.slider_update_signal_id = self.slider.connect('value-changed',
                                                           self.on_slider_changed)
        hbox.pack_start(self.slider, True, True, 2)
        GLib.timeout_add(1000, self.update_slider)

        self.window.show_all()

    def build_gst(self):
        self.gst_pipeline = Gst.Pipeline()
        self.gst_state = Gst.State.NULL

        self.gst_playbin = Gst.ElementFactory.make('playbin')
        self.gst_playbin.set_property('subtitle-font-desc', 'Sans, 18')
        self.gst_pipeline.add(self.gst_playbin)

        bus = self.gst_pipeline.get_bus()
        bus.add_signal_watch()
        bus.enable_sync_message_emission()
        bus.connect('message', self.on_message)
        bus.connect('message::state-changed', self.on_state_changed)
        bus.connect('sync-message::element', self.on_sync_message)

    def gst_play(self):
        response = self.gst_pipeline.set_state(Gst.State.PLAYING)
        if response == Gst.StateChangeReturn.FAILURE:
            print("ERROR: Could not start playing")
            self.gst_pipeline.set_state(Gst.State.NULL)

    def gst_pause(self):
        response = self.gst_pipeline.set_state(Gst.State.PAUSED)
        if response == Gst.StateChangeReturn.FAILURE:
            print("ERROR: Could not pause")
            self.gst_pipeline.set_state(Gst.State.NULL)

    def update_slider(self):
        if self.gst_state == Gst.State.NULL or self.gst_state == Gst.State.READY:
            # Disable slider when not playing
            self.slider.handler_block(self.slider_update_signal_id)
            self.slider.set_sensitive(False)
            self.slider.handler_unblock(self.slider_update_signal_id)

        if self.gst_state != Gst.State.PLAYING:
            return True

        response, duration = self.gst_playbin.query_duration(Gst.Format.TIME)
        if not response:
            raise Exception("Could not get playback duration")

        self.slider.set_range(0, duration / Gst.SECOND)

        response, position = self.gst_playbin.query_position(Gst.Format.TIME)
        if not response:
            raise Exception("Could not get playback position")

        self.slider.handler_block(self.slider_update_signal_id)
        self.slider.set_value(float(position) / Gst.SECOND)
        self.slider.handler_unblock(self.slider_update_signal_id)

        self.slider.set_sensitive(True)

        return True

    def on_slider_changed(self, slider_widget, data=None):
        position = slider_widget.get_value()
        self.gst_playbin.seek_simple(Gst.Format.TIME, Gst.SeekFlags.FLUSH |
                                     Gst.SeekFlags.KEY_UNIT, position *
                                     Gst.SECOND)

    def on_realize_video_window(self, video_widget):
        window = video_widget.get_property('window')
        self.video_window_xid = window.get_xid()

    def on_click_open(self, widget, data=None):
        dialog = Gtk.FileChooserDialog("Choose a video file", self.window,
                                       Gtk.FileChooserAction.OPEN,
                                       (Gtk.STOCK_CANCEL,
                                        Gtk.ResponseType.CANCEL, Gtk.STOCK_OPEN,
                                        Gtk.ResponseType.OK))
        response = dialog.run()

        if response == Gtk.ResponseType.OK:
            self.gst_playbin.set_property('uri', 'file://' +
                                          dialog.get_filename())
            self.gst_play()
        elif response == Gtk.ResponseType.CANCEL:
            print("Cancelled file dialog")

        dialog.destroy()

    def on_click_play(self, widget, data=None):
        if self.gst_state == Gst.State.PLAYING:
            self.gst_pause()
        else:
            self.gst_play()

    def on_click_pause(self, widget, data=None):
        self.gst_pause()

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

    def on_state_changed(self, bus, message):
        if not message.src == self.gst_playbin:
            return

        old, new, pendmessage = message.parse_state_changed()
        self.gst_state = new
        self.update_slider()

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
