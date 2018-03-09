#!/usr/bin/env python

"""
Application to pick single-sentence clips from a video
"""

import sys
import os
import subprocess
import threading
import json
import math

import gi
gi.require_version('Gst', '1.0')
gi.require_version('GstVideo', '1.0')
gi.require_version('Gtk', '3.0')
gi.require_version('GdkX11', '3.0')
from gi.repository import Gst, GObject, Gtk, GdkX11, GstVideo, GLib, Gdk  # noqa: E402


class Main:

    def __init__(self, config_file='config.json'):
        self.config_file = config_file
        self.config = {}
        with open(config_file) as config_file:
            self.config = json.load(config_file)

        self.build_ui()
        self.build_gst()

        self.filename = ''
        self.current_subtitle = ""
        self.current_subtitle_duration = 0
        self.current_subtitle_start = 0

        self.current_scene_start = 0
        self.next_scene_start = 0
        self.record_current_scene = False

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

        self.subtitle_label = Gtk.Label("...")
        vbox.pack_start(self.subtitle_label, False, False, 2)

        hbox = Gtk.HBox()
        hbox.set_border_width(10)
        vbox.pack_start(hbox, False, False, 2)

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

        self.pick_button = (
            Gtk.Button(image=Gtk.Image.new_from_stock(Gtk.STOCK_COLOR_PICKER,
                                                      Gtk.IconSize.BUTTON)))
        self.pick_button.connect('clicked', self.on_click_pick)
        hbox.pack_start(self.pick_button, False, False, 2)

        self.record_button = (
            Gtk.ToggleButton(image=Gtk.Image.new_from_stock(Gtk.STOCK_MEDIA_RECORD,
                                                            Gtk.IconSize.BUTTON)))
        self.record_button.set_active(False)
        self.record_button.set_relief(Gtk.ReliefStyle.NONE)
        self.record_button_clicked_id = self.record_button.connect('toggled', self.on_click_record)
        hbox.pack_start(self.record_button, False, False, 2)

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

        self.gst_subtitle_sink = Gst.ElementFactory.make('appsink')
        self.gst_subtitle_sink.set_property('emit-signals', True)
        self.gst_subtitle_sink.connect('new-sample', self.on_subtitle_sample, self.gst_subtitle_sink)

        self.gst_playbin.set_property('text-sink', self.gst_subtitle_sink)

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

    def clip_id(self):
        filename = os.path.basename(self.filename)
        return (filename.split('.')[-2].split('-')[-1] +
                str(self.current_subtitle_start))

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

        self.slider.handler_block(self.slider_update_signal_id)
        self.slider.set_range(0, duration / Gst.SECOND)
        self.slider.handler_unblock(self.slider_update_signal_id)

        response, position = self.gst_playbin.query_position(Gst.Format.TIME)
        if not response:
            raise Exception("Could not get playback position")

        self.slider.handler_block(self.slider_update_signal_id)
        self.slider.set_value(float(position) / Gst.SECOND)
        self.slider.set_sensitive(True)
        self.slider.handler_unblock(self.slider_update_signal_id)

        return True

    def save_current_scene(self):
        print("Saving current scene")

        scenes = []
        with open(self.filename + '.json') as config_file:
            scenes = json.load(config_file)['frames']

        # Get current playback position
        response, position = self.gst_playbin.query_position(Gst.Format.TIME)
        if not response:
            raise Exception("Could not get playback position")
        current_time = float(position) / Gst.SECOND

        # Get current scene start point
        # Get next scene start point
        current_scene = None
        next_scene = None
        for i, scene in enumerate(scenes):
            if float(scene['pkt_pts_time']) > float(current_time):
                assert(i > 0)
                next_scene = scene
                current_scene = scenes[i-1]
                break

        self.current_scene_start = float(current_scene['pkt_pts_time'])
        self.next_scene_start = float(next_scene['pkt_pts_time'])

        print("Current scene start: " + str(self.current_scene_start))
        print("Next scene start: " + str(self.next_scene_start))
        print("Position: " + str(current_time))

        # Go to the start of the current scene
        self.gst_playbin.seek_simple(Gst.Format.TIME, Gst.SeekFlags.FLUSH,
                                     self.current_scene_start * Gst.SECOND)

        # Skip until the first subtitle completely within the scene is started
        self.record_current_scene = True
        self.record_button.handler_block(self.record_button_clicked_id)
        self.record_button.set_active(True)
        self.record_button.handler_unblock(self.record_button_clicked_id)

        # Save clip
        # Go to the next subtitle
        # Keep saving clips until the current clip ends after the current scene

    def pick(self):
        # Find the current position
        response, position = self.gst_playbin.query_position(Gst.Format.TIME)
        if not response:
            raise Exception("Could not get playback position")

        # Find the start and endpoints of the current subtitle
        start = float(self.current_subtitle_start) / Gst.SECOND
        duration = float(self.current_subtitle_duration) / Gst.SECOND

        # Extract the image sequence for this subtitle with ffmpeg
        subprocess.check_call('mkdir -p ' + self.config['image_root'], shell=True)
        command = ('ffmpeg -ss ' + str(start) +
                   ' -i \'' + self.filename + '\' -t ' +
                   str(duration) +
                   ' ' + self.config['image_root'] + self.clip_id() +
                   '-%d' + self.config['image_extension'])
        print(command)
        subprocess.check_call(command, shell=True)

        # Store the results in the JSON data file
        self.save_clip()

    def save_clip(self, config_file='config.json'):
        video_pad = self.gst_playbin.emit('get-video-pad', 0)
        response = video_pad.get_current_caps().get_structure(0).get_fraction('framerate')
        if not response[0]:
            raise Exception("Could not get framerate")

        framerate = float(response[1]) / float(response[2])
        start = math.floor(float(self.current_subtitle_start) * framerate /
                           Gst.SECOND)
        duration = math.ceil(float(self.current_subtitle_duration) * framerate
                             / Gst.SECOND)

        clip = {
            'id': self.clip_id(),
            'start': start,
            'end': start + duration,
            'scale': 4.5,
            'center': [568, 696],  # FIXME
            'points_2d': [],
            'points_3d': [],
            'subtitle': self.current_subtitle
        }

        self.config['clips'].append(clip)

        with open(self.config_file, 'w') as config_file:
            json.dump(self.config, config_file, indent=2)
            print("Wrote " + str(config_file))

    def seek_to(self, seconds):
        self.gst_playbin.seek_simple(Gst.Format.TIME, Gst.SeekFlags.FLUSH |
                                     Gst.SeekFlags.KEY_UNIT,
                                     seconds * Gst.SECOND)

    def on_slider_changed(self, slider_widget, data=None):
        position = slider_widget.get_value()
        self.seek_to(position)

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
            self.filename = dialog.get_filename()
            self.gst_playbin.set_property('uri', 'file://' + self.filename)
            self.gst_play()

            if not os.path.isfile(self.filename + '.json'):
                print("WARNING: Expected scene info file " + self.filename
                      + '.json, but none was found.')
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

    def on_click_pick(self, widget, data=None):
        self.pick()

    def on_click_record(self, widget, data=None):
        if self.record_current_scene:
            self.record_current_scene = False
            self.record_button.handler_block(self.record_button_clicked_id)
            self.record_button.set_active(False)
            self.record_button.handler_unblock(self.record_button_clicked_id)
        else:
            self.save_current_scene()

    def on_subtitle_sample(self, sink, data):
        sample = sink.emit('pull-sample')
        buf = sample.get_buffer()

        self.current_subtitle = str(buf.extract_dup(0, buf.get_size()))
        self.subtitle_label.set_markup(self.current_subtitle)

        self.current_subtitle_duration = buf.duration

        response, position = self.gst_playbin.query_position(Gst.Format.TIME)
        if not response:
            raise Exception("Could not get playback position")
        self.current_subtitle_start = position

        # If recording current scene, save the clip
        if self.record_current_scene:
            print("Maybe recording this clip")
            current_subtitle_end = (self.current_subtitle_start +
                                    self.current_subtitle_duration) / Gst.SECOND

            if current_subtitle_end >= self.next_scene_start:
                print("Clip subtitles end after current scene")
                self.record_current_scene = False
                self.record_button.handler_block(self.record_button_clicked_id)
                self.record_button.set_active(False)
                self.record_button.handler_unblock(self.record_button_clicked_id)
            else:
                print("Saving current clip")
                threading.Thread(target=self.pick, name="Clip extractor").start()
                # self.pick()
                # self.seek_to(current_subtitle_end)

        return False

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
    GObject.threads_init()
    Gtk.init(sys.argv)
    Gst.init(sys.argv)
    Main()
    Gtk.main()
