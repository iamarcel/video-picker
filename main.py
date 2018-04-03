#!/usr/bin/env python

"""
Application to pick single-sentence clips from a video
"""

import datetime
import sys
import os
import shutil
import subprocess
import json
import math

import cairo
import gi
gi.require_version('Gst', '1.0')
gi.require_version('GstVideo', '1.0')
gi.require_version('Gtk', '3.0')
gi.require_version('GdkX11', '3.0')
gi.require_foreign('cairo')
from gi.repository import Gst, GObject, Gtk, GdkX11, GstVideo, GLib, Gdk  # noqa: E402


class Main:

    def __init__(self, config_file='config.json'):
        self.config_file = config_file
        self.config = {}
        with open(config_file) as config_file:
            self.config = json.load(config_file)

        self.video_scale = 1.0
        self.video_margin = (0, 0)
        self.center_position = (0, 0)
        self.detection_scale = 4.5
        self.gst_src = None
        self.window_size = (50, 50)

        self.build_gst()
        self.build_ui()

        self.filename = ''
        self.framerate = 1.0
        self.current_subtitle = ""
        self.current_subtitle_duration = 0
        self.current_subtitle_start = 0

        self.clips_processing = []
        self.current_scene_start = 0
        self.next_scene_start = 0
        self.record_current_scene = False

        self.split_sub_lines = False

    def build_ui(self):
        self.window = Gtk.Window(Gtk.WindowType.TOPLEVEL)
        self.window.set_title("Video Picker")
        self.window.set_default_size(960, 640)
        self.window.connect('destroy', Gtk.main_quit, 'WM Destroy')

        self.accelerators = Gtk.AccelGroup()
        self.accelerators.connect(
            Gdk.keyval_from_name('O'), Gdk.ModifierType.CONTROL_MASK, 0,
            self.on_click_open)
        self.accelerators.connect(
            Gdk.keyval_from_name('R'), Gdk.ModifierType.CONTROL_MASK, 0,
            self.on_click_record)
        self.accelerators.connect(
            Gdk.keyval_from_name('Q'), Gdk.ModifierType.CONTROL_MASK, 0,
            self.on_click_exit)
        self.window.add_accel_group(self.accelerators)

        vbox = Gtk.VBox()
        self.window.add(vbox)

        # self.overlay = Gtk.Overlay()
        # vbox.add(self.overlay)

        self.video_window = Gtk.DrawingArea()
        self.video_window.connect('realize', self.on_realize_video_window)
        self.video_window.connect('size-allocate',
                                  self.on_video_window_resize)
        self.video_window.connect('button-press-event',
                                  self.on_video_window_click)
        self.video_window.connect('motion-notify-event',
                                  self.on_video_window_click)
        self.video_window.connect('scroll-event',
                                  self.on_video_window_scroll)
        self.video_window.set_events(Gdk.EventMask.KEY_PRESS_MASK |
                                     Gdk.EventMask.POINTER_MOTION_MASK |
                                     Gdk.EventMask.BUTTON_MOTION_MASK |
                                     Gdk.EventMask.SCROLL_MASK)
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

        self.record_button = (Gtk.ToggleButton(
            image=Gtk.Image.new_from_stock(Gtk.STOCK_MEDIA_RECORD,
                                           Gtk.IconSize.BUTTON)))
        self.record_button.set_active(False)
        self.record_button.set_relief(Gtk.ReliefStyle.NONE)
        self.record_button_clicked_id = self.record_button.connect(
            'toggled', self.on_click_record)
        hbox.pack_start(self.record_button, False, False, 2)

        self.subtitle_split_toggle = Gtk.CheckButton(label="Split Sub Lines")
        self.subtitle_split_toggle.connect('toggled', self.on_toggle_sub_split)
        hbox.pack_start(self.subtitle_split_toggle, False, False, 2)

        self.exit_button = Gtk.Button("Exit")
        self.exit_button.connect('clicked', self.on_click_exit)
        hbox.pack_start(self.exit_button, False, False, 2)

        self.slider = Gtk.HScale.new_with_range(0, 100, 0.5)
        self.slider.set_draw_value(False)
        self.slider_update_signal_id = self.slider.connect(
            'value-changed', self.on_slider_changed)
        hbox.pack_start(self.slider, True, True, 2)
        GLib.timeout_add(1000, self.update_slider)

        self.window.show_all()

    def build_gst(self):
        self.gst_pipeline = Gst.Pipeline()
        self.gst_state = Gst.State.NULL

        self.gst_src = Gst.ElementFactory.make('playbin')
        self.gst_pipeline.add(self.gst_src)

        # Set up video output bin
        # (GhostPad:sink) my-video-bin
        #     (queue > cairooverlay > videoconvert > autovideosink)
        self.gst_video_bin = Gst.Bin.new('my-video-bin')

        self.gst_video_queue = Gst.ElementFactory.make('queue')
        self.gst_video_bin.add(self.gst_video_queue)

        self.gst_overlay = Gst.ElementFactory.make('cairooverlay')
        self.gst_overlay.connect('draw', self.on_draw_scale_preview)
        self.gst_video_bin.add(self.gst_overlay)
        self.gst_video_queue.link(self.gst_overlay)

        self.gst_convert = Gst.ElementFactory.make('videoconvert')
        self.gst_video_bin.add(self.gst_convert)
        self.gst_overlay.link(self.gst_convert)

        self.gst_video_sink = Gst.ElementFactory.make('autovideosink')
        self.gst_video_bin.add(self.gst_video_sink)
        self.gst_convert.link(self.gst_video_sink)

        self.gst_video_sink_pad = self.gst_video_queue.get_static_pad('sink')
        self.gst_video_ghost_pad = Gst.GhostPad.new(
            'sink', self.gst_video_sink_pad)
        self.gst_video_ghost_pad.set_active(True)
        self.gst_video_bin.add_pad(self.gst_video_ghost_pad)

        self.gst_src.set_property('video-sink', self.gst_video_bin)

        self.gst_subtitle_sink = Gst.ElementFactory.make('appsink')
        self.gst_subtitle_sink.set_property('emit-signals', True)
        self.gst_subtitle_sink.connect('new-sample', self.on_subtitle_sample, self.gst_subtitle_sink)
        self.gst_src.set_property('text-sink', self.gst_subtitle_sink)

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

    def clip_is_processed(self, id):
        if id in self.clips_processing:
            return True

        for clip in self.config['clips']:
            if clip['id'] == id:
                return True

        return False

    def update_slider(self):
        if self.gst_state == Gst.State.NULL or self.gst_state == Gst.State.READY:
            # Disable slider when not playing
            self.slider.handler_block(self.slider_update_signal_id)
            self.slider.set_sensitive(False)
            self.slider.handler_unblock(self.slider_update_signal_id)

        if self.gst_state != Gst.State.PLAYING:
            return True

        response, duration = self.gst_src.query_duration(Gst.Format.TIME)
        if not response:
            raise Exception("Could not get playback duration")

        self.slider.handler_block(self.slider_update_signal_id)
        self.slider.set_range(0, duration / Gst.SECOND)
        self.slider.handler_unblock(self.slider_update_signal_id)

        response, position = self.gst_src.query_position(Gst.Format.TIME)
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
        response, position = self.gst_src.query_position(Gst.Format.TIME)
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
        self.gst_src.seek_simple(Gst.Format.TIME, Gst.SeekFlags.FLUSH,
                                 self.current_scene_start * Gst.SECOND)

        # Skip until the first subtitle completely within the scene is started
        self.record_current_scene = True
        self.record_button.handler_block(self.record_button_clicked_id)
        self.record_button.set_active(True)
        self.record_button.handler_unblock(self.record_button_clicked_id)

        shutil.copyfile(self.config_file, self.config_file + '.bak-'
                        + datetime.datetime.now().isoformat())

        # Save clip
        # Go to the next subtitle
        # Keep saving clips until the current clip ends after the current scene

    def pick(self):
        # Find the current position
        response, position = self.gst_src.query_position(Gst.Format.TIME)
        if not response:
            raise Exception("Could not get playback position")

        # Find the start and endpoints of the current subtitle
        start = float(self.current_subtitle_start) / Gst.SECOND
        duration = float(self.current_subtitle_duration) / Gst.SECOND

        # Extract the image sequence for this subtitle with ffmpeg
        subprocess.check_call('mkdir -p ' + self.config['image_root'], shell=True)
        command = ('ffmpeg' +
                   ' -loglevel quiet' +
                   ' -ss ' + str(start) +
                   ' -i \'' + self.filename + '\' -t ' +
                   str(duration) +
                   ' ' + self.config['image_root'] + self.clip_id() +
                   '-%6d' + self.config['image_extension'])
        print(command)
        subprocess.check_call(command, shell=True)

        # Store the results in the JSON data file
        self.save_clip()

    def save_clip(self, config_file='config.json'):
        id = self.clip_id()
        if self.clip_is_processed(id):
            return
        self.clips_processing.append(id)

        framerate = self.framerate
        start = math.floor(float(self.current_subtitle_start) * framerate /
                           Gst.SECOND)
        duration = math.ceil(float(self.current_subtitle_duration) * framerate
                             / Gst.SECOND)
        if duration <= 15:
            return  # Skip if shorter than 15 frames

        clip = {
            'id': id,
            'start': start,
            'end': start + duration,
            'scale': self.detection_scale,
            'center': list(self.center_position),
            'points_2d': [],
            'points_3d': [],
            'subtitle': self.current_subtitle
        }

        self.config['clips'].append(clip)

        with open(self.config_file, 'w') as config_file:
            json.dump(self.config, config_file, indent=2)
            print("Wrote " + str(config_file))

        self.clips_processing.remove(id)

    def seek_to(self, seconds):
        self.gst_src.seek_simple(Gst.Format.TIME, Gst.SeekFlags.FLUSH |
                                     Gst.SeekFlags.KEY_UNIT,
                                     seconds * Gst.SECOND)

    def update_video_margin(self):
        if not self.gst_src:
            return

        pad = self.gst_src.emit('get-video-pad', 0)
        if not pad:  # Pad is not ready yet
            return

        response = pad.get_current_caps().get_structure(0)
        width = response.get_int('width')
        if not width[0]:
            raise Exception("Could not get video width")
        width = width.value

        height = response.get_int('height')
        if not height[0]:
            raise Exception("Could not get video height")
        height = height.value

        window_width, window_height = self.window_size

        # Calculate resizing
        fit_x_scale = float(window_width) / float(width)
        fit_y_scale = float(window_height) / float(height)
        scale = min(fit_x_scale, fit_y_scale)

        self.video_scale = scale
        self.video_margin = (
            int((window_width - float(width) * scale) / 2),
            int((window_height - float(height) * scale) / 2),
        )

    def set_center_position(self, position):
        self.center_position = position

    def on_slider_changed(self, slider_widget, data=None):
        position = slider_widget.get_value()
        self.seek_to(position)

    def on_realize_video_window(self, video_widget):
        window = video_widget.get_property('window')
        self.video_window_xid = window.get_xid()

    def on_click_open(self, *args, **kwargs):
        dialog = Gtk.FileChooserDialog("Choose a video file", self.window,
                                       Gtk.FileChooserAction.OPEN,
                                       (Gtk.STOCK_CANCEL,
                                        Gtk.ResponseType.CANCEL, Gtk.STOCK_OPEN,
                                        Gtk.ResponseType.OK))
        response = dialog.run()

        if response == Gtk.ResponseType.OK:
            self.filename = dialog.get_filename()
            self.gst_src.set_property('uri', 'file://' + self.filename)
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

    def on_click_exit(self, *args, **kwargs):
        self.gst_pipeline.set_state(Gst.State.NULL)
        Gtk.main_quit()

    def on_click_pick(self, widget, data=None):
        self.pick()

    def on_click_record(self, *args, **kwargs):
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

        subtitle = buf.extract_dup(0, buf.get_size()).decode('UTF-8')

        if self.split_sub_lines:
            self.current_subtitle = subtitle.split('\n')[-1]
        else:
            self.current_subtitle = subtitle.replace('\n', ' ')

        self.subtitle_label.set_markup(self.current_subtitle)

        self.current_subtitle_duration = buf.duration
        self.current_subtitle_start = buf.pts

        # If recording current scene, save the clip
        if self.record_current_scene:
            print("Maybe recording this clip")
            current_subtitle_end = (self.current_subtitle_start +
                                    self.current_subtitle_duration) / Gst.SECOND
            current_subtitle_start = self.current_subtitle_start / Gst.SECOND

            if current_subtitle_end >= self.next_scene_start:
                print("Clip subtitles end after current scene")
                self.record_current_scene = False
                self.record_button.handler_block(self.record_button_clicked_id)
                self.record_button.set_active(False)
                self.record_button.handler_unblock(
                    self.record_button_clicked_id)
            elif current_subtitle_start < self.current_scene_start:
                print("Clip subtitles start before current scene")
            else:
                print("Saving current clip")
                self.pick()
                # self.seek_to(current_subtitle_end)

        return False

    def on_video_window_click(self, widget, event):
        x = (float(event.x) - self.video_margin[0]) / self.video_scale
        y = (float(event.y) - self.video_margin[1]) / self.video_scale
        self.center_position = (x, y)

    def on_toggle_sub_split(self, widget):
        self.split_sub_lines = widget.get_active()

    def on_video_window_scroll(self, widget, event):
        self.detection_scale -= 0.16 * event.delta_y

    def on_video_window_resize(self, widget, rectangle):
        self.window_size = (rectangle.width, rectangle.height)
        self.update_video_margin()

    def on_draw_scale_preview(self, overlay, draw, timestamp, duration):
        center_x = self.center_position[0]
        center_y = self.center_position[1]
        top_x = center_x - self.detection_scale * 64
        top_y = center_y - self.detection_scale * 64
        edge_length = self.detection_scale * 128

        draw.save()

        draw.rectangle(top_x, top_y, edge_length, edge_length)
        draw.rectangle(center_x, center_y, 2, 2)

        draw.set_tolerance(0.1)
        draw.set_line_width(5)
        draw.set_source_rgba(0.0, 0.0, 0.0, 0.3)
        draw.stroke()

        draw.rectangle(top_x, top_y, edge_length, edge_length)
        draw.rectangle(center_x, center_y, 2, 2)

        draw.set_line_width(2)
        if self.record_current_scene:
            draw.set_source_rgba(1.0, 0.0, 0.0, 0.8)
        else:
            draw.set_source_rgba(1.0, 1.0, 1.0, 0.7)
        draw.stroke()

        draw.restore()

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
        if not message.src == self.gst_src:
            return

        old, new, pendmessage = message.parse_state_changed()
        self.gst_state = new
        self.update_slider()

        if self.gst_state == Gst.State.PAUSED:
            pad = self.gst_src.emit('get-video-pad', 0)
            response = pad.get_current_caps().get_structure(0).get_fraction('framerate')
            self.framerate = float(response[1]) / float(response[2])

        if self.gst_state == Gst.State.PLAYING:
            self.update_video_margin()

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
    # Gst.debug_set_active(True)
    # Gst.debug_set_default_threshold(3)
    Main()
    Gtk.main()
