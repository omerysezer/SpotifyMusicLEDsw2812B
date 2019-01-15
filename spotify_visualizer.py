# !/usr/bin/env python3
import apa102
from credentials import USERNAME, SPOTIPY_CLIENT_ID, SPOTIPY_CLIENT_SECRET, SPOTIPY_REDIRECT_URI
import json
import numpy as np
from scipy.interpolate import interp1d
import spotipy
import spotipy.util as util
import threading
import time


class SpotifyVisualizer:
    """A class that allows for multi-threaded music visualization via the Spotify API, a Raspberry Pi, and an LED strip.

    This class allows for multi-threaded music visualization via the Spotify API and an APA102 LED strip. This code was
    developed and tested using a 240-pixel (4 meter) Adafruit Dotstar LED strip, a Raspberry Pi 3 Model B and my
    personal Spotify account. After initializing an instance of this class, simply call visualize() to begin
    visualization (alternatively, simply run this module). Visualization will continue until the program is interrupted
    or terminated. There are 4 threads: one for visualization, one for periodically syncing the playback position with
    the Spotify API, one for loading chunks of track data, and one to periodically check if the user's current track has
    changed.
    Currently, loudness and pitch data are used to generate and display visualizations on the LED strip. Loudness is
    used to determine how many pixels to light (growing from the center of the strip towards the ends). At any given
    moment, the number of lit pixels is segmented into 12 equal-length zones (one zone for each of the 12 major pitch
    keys). Each zone fades from start_color to its corresponding end color in end_colors. If the pitch key corresponding
    to a zone is very strong (high presence), that zone will be set to its end color. If the pitch key corresponding to
    a zone is very weak, then the zone will be set to start_color. If the pitch key corresponding to a zone is of
    intermediate strength, then the zone will be set to a color value between start_color and its end color.
    Additionally, A fade effect is applied to the ends of each zone; the color of the middle pixel of a zone will be set
    purely based on the strength of the corresponding pitch. Pixels towards the end of each zone, however, will fade
    back towards start_color.

    Args:
        num_pixels (int): The number of pixels (LEDs) supported by the LED strip.

    Attributes:
            beats (list): a list of dict objects, where each dict object describes a beat (fetched from Spotify API).
            beat_info (dict): a dict mapping a beat's start time to a tuple of its properties (duration and confidence).
            buffer_lock (threading.Lock): a lock for accessing/modifying the buffers of interpolated functions.
            data_segments (list): data segments to be parsed and interpreted (fetched from Spotify API).
            end_colors (dict): a dict of 12 RGB 3-tuples; map pitch zone indices to the end-gradient-color for the zone.
            interpolated_beats_buffer (list): producer-consumer buffer holding interpolated beat start time functions.
            interpolated_loudness_buffer (list): producer-consumer buffer holding interpolated loudness functions.
            interpolated_pitch_buffer (list): producer-consumer buffer holding lists of interpolated pitch functions.
            interpolated_timbre_buffer (list): producer-consumer buffer holding lists of interpolated timbre functions.
            num_pixels (int): the number of pixels (LEDs) on the LED strip.
            permission_scopes (str): a space-separated str of the required permission scopes over the user's account.
            playback_pos (float): the current playback position (offset into track in seconds) of the visualization.
            pos_lock (threading.Lock): a lock for accessing/modifying playback_pos.
            should_terminate (bool): a variable watched by all child threads (child threads exit if set to True).
            sp_gen (Spotify): Spotify object to handle main thread's interaction with the Spotify API.
            sp_load (Spotify): Spotify object to handle data loading thread's interaction with the Spotify API.
            sp_skip (Spotify): Spotify object to handle skip detection thread's interaction with the Spotify API.
            sp_sync (Spotify): Spotify object to handle synchronization thread's interaction with the Spotify API.
            sp_vis (Spotify): Spotify object to handle visualization thread's interaction with the Spotify API.
            start_color (tuple): a 3-tuple of ints for the RGB value representing the start color of the pitch gradient.
            strip (APA102): APA102 object to handle interfacing with the LED strip.
            track (dict): contains information about the track that is being visualized.
            track_duration (float): the duration in seconds of the track that is being visualized.
    """

    def __init__(self, num_pixels):
        self.beats = []
        self.beat_info = {}
        self.buffer_lock = threading.Lock()
        self.data_segments = []
        self.end_colors = {
            0: (0xFF, 0xFF, 0xFF),
            1: (0xF5, 0xE7, 0xE7),
            2: (0xEC, 0xD0, 0xD0),
            3: (0xE3, 0xB9, 0xB9),
            4: (0xD9, 0xA2, 0xA2),
            5: (0xD0, 0x8B, 0x8B),
            6: (0xC7, 0x73, 0x73),
            7: (0xBE, 0x5C, 0x5C),
            8: (0xB4, 0x45, 0x45),
            9: (0xAB, 0x2E, 0x2E),
            10: (0xA2, 0x17, 0x17),
            11: (0x99, 0, 0)
        }
        self.interpolated_beats_buffer = []
        self.interpolated_label_func = None
        self.interpolated_loudness_buffer = []
        self.interpolated_pitch_buffer = []
        self.interpolated_timbre_buffer = []
        self.num_pixels = num_pixels
        self.permission_scopes = "user-modify-playback-state user-read-currently-playing user-read-playback-state"
        self.playback_pos = 0
        self.pos_lock = threading.Lock()
        self.should_terminate = False
        self.sp_gen = self.sp_load = self.sp_skip = self.sp_sync = self.sp_vis = None
        self.start_color = (0, 0, 255)
        self.strip = apa102.APA102(num_led=num_pixels, global_brightness=30, mosi=10, sclk=11, order='rgb')
        self.track = None
        self.track_duration = None

    def authorize(self):
        """Handle the authorization workflow for the Spotify API
        """
        token = util.prompt_for_user_token(USERNAME,
                                           self.permission_scopes,
                                           SPOTIPY_CLIENT_ID,
                                           SPOTIPY_CLIENT_SECRET,
                                           SPOTIPY_REDIRECT_URI)
        if token:
            # Instantiate multiple Spotify objects because sharing a Spotify object can block threads
            self.sp_gen = spotipy.Spotify(auth=token)
            self.sp_vis = spotipy.Spotify(auth=token)
            self.sp_sync = spotipy.Spotify(auth=token)
            self.sp_load = spotipy.Spotify(auth=token)
            self.sp_skip = spotipy.Spotify(auth=token)
            text = "Successfully connected to {}'s account.".format(self.sp_gen.me()["display_name"])
            print(SpotifyVisualizer._make_text_effect(text, ["green"]))
        else:
            raise Exception("Unable to authenticate Spotify user.")

    def get_track(self):
        """Fetches current track (waits for a track if necessary), starts it from beginning, and loads some track data.
        """
        text = "Waiting for an active Spotify track to start visualization."
        print(SpotifyVisualizer._make_text_effect(text, ["green", "bold"]))
        while not self.track:
            self.track = self.sp_gen.current_user_playing_track()
            time.sleep(1)
        track_name = self.track["item"]["name"]
        artists = ', '.join((artist["name"] for artist in self.track["item"]["artists"]))
        text = "Loaded track: {} by {}.".format(track_name, artists)
        print(SpotifyVisualizer._make_text_effect(text, ["green"]))
        self.track_duration = self.track["item"]["duration_ms"] / 1000
        self._reset_track()
        self._load_track_data()

    def sync(self):
        """Syncs visualizer with Spotify playback. Called asynchronously (worker thread).
        """
        track_progress = self.sp_sync.current_user_playing_track()["progress_ms"] / 1000
        text = "Syncing track to position: {}.".format(track_progress)
        print(SpotifyVisualizer._make_text_effect(text, ["green", "bold"]))
        self.pos_lock.acquire()
        self.playback_pos = track_progress
        self.pos_lock.release()

    def visualize(self):
        """Coordinate visualization by spawning the appropriate threads.

        There are 4 threads: one for visualization, one for periodically syncing the playback position with the Spotify
        API, one for loading chunks of track data, and one to periodically check if the user's current track has
        changed.
        """
        self.authorize()
        while True:
            self._reset()
            self.get_track()

            # Start threads and wait for them to exit
            threads = [
                threading.Thread(target=self._visualize),
                threading.Thread(target=self._continue_loading_data),
                threading.Thread(target=self._continue_syncing),
                threading.Thread(target=self._continue_checking_if_skip)
            ]
            for thread in threads:
                thread.start()
            text = "Started visualization."
            print(SpotifyVisualizer._make_text_effect(text, ["green"]))
            for thread in threads:
                thread.join()
            text = "Visualization finished."
            print(SpotifyVisualizer._make_text_effect(text, ["green"]))

    def _apply_gradient_fade(self, r, g, b, strength):
        """Fade the passed RGB value towards start_color based on strength

        Note that a strength value of 0.0 results in the start color of the gradient, and a strength value of 1.0
        results in the same RGB color that was passed (no fade is applied).

        Args:
             r (int): represents the red value (in range [0, 0xFF]) of the RGB color to fade.
             b (int): represents the blue value (in range [0, 0xFF]) of the RGB color to fade.
             g (int): represents the green value (in range [0, 0xFF]) of the RGB color to fade.
             strength (float): a strength value representing how strong the RGB color should be (in range [0.0, 1.0]).

        Returns:
            a 3-tuple of ints representing the RGB value with fade applied.
        """
        start_r, start_g, start_b = self.start_color
        r_diff, g_diff, b_diff = r - start_r, g - start_g, b - start_b

        faded_r = start_r + int(strength * r_diff)
        faded_g = start_g + int(strength * g_diff)
        faded_b = start_b + int(strength * b_diff)

        return faded_r, faded_g, faded_b

    def _calculate_zone_color(self, pitch_strength, zone_index):
        """Calculate the color to visualize based on the pitch/zone index and corresponding pitch strength.

        The visualizer divides the lit portion of the strip into 12 equal-length zones, one for each of the 12 major
        pitch keys. This function calculates what color should be displayed in the zone specified by zone_index if the
        corresponding pitch has strength pitch_strength (0.0 corresponds to lowest strength, 1.0 corresponds to maximum
        strength).

        Args:
            pitch_strength (float): a value representing how strong or present the pitch is (normalized to [0.0, 1.0]).
            zone_index (int): an index in range [0, 11] corresponding to the zone/pitch key.

        Returns:
            a 3-tuple of ints representing the RGB value that should be displayed in the zone specified by zone_index.
        """
        if pitch_strength < 0.0:
            pitch_strength = 0.0
        elif pitch_strength > 1.0:
            pitch_strength = 1.0

        start_r, start_g, start_b = self.start_color
        end_r, end_g, end_b = self.end_colors[zone_index]
        r_diff, g_diff, b_diff = end_r - start_r, end_g - start_g, end_b - start_b

        r = start_r + int(pitch_strength * r_diff)
        g = start_g + int(pitch_strength * g_diff)
        b = start_b + int(pitch_strength * b_diff)

        return r, g, b

    def _continue_checking_if_skip(self, wait=0.33):
        """Continuously checks if the user's playing track has changed. Called asynchronously (worker thread).

        If the user's currently playing track has changed (is different from track), then this function pauses the
        user's playback and sets should_terminate to True, resulting in the termination of all worker threads.

        Args:
            wait (float): the amount of time in seconds to wait between each check.
        """
        track = self.sp_skip.current_user_playing_track()
        while track["item"]["id"] == self.track["item"]["id"]:
            time.sleep(wait)
            track = self.sp_skip.current_user_playing_track()
        self.sp_skip.pause_playback()
        self.should_terminate = True
        text = "A skip has occurred."
        print(SpotifyVisualizer._make_text_effect(text, ["blue", "bold"]))

    def _continue_loading_data(self, wait=0.5):
        """Continuously loads and prepares chunks of data. Called asynchronously (worker thread).

        Args:
            wait (float): the amount of time in seconds to wait between each call to _load_track_data().
        """
        while len(self.data_segments) != 0 and not self.should_terminate:
            self._load_track_data()
            time.sleep(wait)
        text = "Killing data loading thread."
        print(SpotifyVisualizer._make_text_effect(text, ["red", "bold"]))
        exit(0)

    def _continue_syncing(self, wait=0.05):
        """Repeatedly syncs visualization playback position with the Spotify API.

        Args:
            wait (float): the amount of time in seconds to wait between each sync.
        """
        pos = self.playback_pos
        while round(self.track_duration - pos) != 0 and not self.should_terminate:
            self.sync()
            time.sleep(wait)
            pos = self.playback_pos
        text = "Killing synchronization thread."
        print(SpotifyVisualizer._make_text_effect(text, ["red", "bold"]))
        exit(0)

    def _get_buffers_for_pos(self, pos):
        """Find the interpolated functions that have the specified position within their bounds via binary search.

        Args:
            pos (float): the playback position to find interpolated functions for.

        Returns:
             a 4-tuple of interp1d objects (loudness, pitch, timbre and beats functions) or None if search fails.
        """
        self.buffer_lock.acquire()

        # Binary search for interpolated loudness, pitch and timbre functions
        start, end, lpt_index = 0, len(self.interpolated_loudness_buffer) - 1, None
        while start <= end:
            mid = start + (end - start) // 2
            lower_bound, upper_bound, _ = self.interpolated_loudness_buffer[mid]
            if lower_bound <= pos <= upper_bound:
                lpt_index = mid
                break
            if pos < lower_bound:
                end = mid - 1
            if pos > upper_bound:
                start = mid + 1

        # Binary search for interpolated beats function
        start, end, b_index = 0, len(self.interpolated_beats_buffer) - 1, None
        while start <= end:
            mid = start + (end - start) // 2
            lower_bound, upper_bound, _ = self.interpolated_beats_buffer[mid]
            if lower_bound <= pos <= upper_bound:
                b_index = mid
                break
            if pos < lower_bound:
                end = mid - 1
            if pos > upper_bound:
                start = mid + 1

        to_return = None
        if lpt_index is not None and b_index is not None:
            to_return = (
                self.interpolated_loudness_buffer[lpt_index][-1],
                self.interpolated_pitch_buffer[lpt_index][-1],
                self.interpolated_timbre_buffer[lpt_index][-1],
                self.interpolated_beats_buffer[b_index][-1]
            )
        self.buffer_lock.release()
        return to_return

    def _load_track_data(self, chunk_length=12):
        """Obtain track data from the Spotify API and run necessary analysis to generate data needed for visualization.

        Each call to this function analyzes the next chunk_length seconds of track data and produces the appropriate
        interpolated loudness, pitch and timbre functions. These interpolated functions are added to their
        corresponding buffers.

        Args:
            chunk_length (float): the number of seconds of track data to analyze.
        """
        # If necessary, get audio data for the track from the Spotify API and pad data to cover the full track length
        if not self.data_segments or not self.beats:
            analysis = self.sp_load.audio_analysis(self.track["item"]["id"])
            self.data_segments.append(
                {
                    "start": -0.1,
                    "loudness_start": -25.0,
                    "pitches": 12*[0],
                    "timbre": 12*[0]
                }
            )
            self.data_segments += analysis["segments"]
            self.data_segments.append(
                {
                    "start": self.track_duration + 0.1,
                    "loudness_start": -25.0, "pitches": 12*[0],
                    "timbre": 12*[0]
                }
            )
            self.beats.append(
                {
                    "start": -0.1,
                    "duration": 0.0,
                    "confidence": 0.0
                }
            )
            self.beats += analysis["beats"]
            self.beats.append(
                {
                    "start": self.track_duration + 0.1,
                    "duration": 0.0,
                    "confidence": 0.0
                }
            )

        # Load precomputed KMeans cluster data (based on timbre) from file if available for the current track
        if self.track["name"] in ["Crawl Outta Love", "Ruin My Life"]:
            filename = '_'.join((self.track["name"] + " timbre data").split(' ')) + ".txt"
            with open(filename) as file:
                kmeans_data = json.loads(file)
            timestamps = kmeans_data["timestamps"]
            cluster_labels = kmeans_data["cluster_labels"]
            self.interpolated_label_func = interp1d(
                timestamps,
                cluster_labels,
                kind='previous',
                assume_sorted=True
            )

        # Extract the next chunk_length seconds of useful loudness, pitch and timbre data
        s_t, l, pitch_lists, timbre_lists = [], [], [], []
        i = 0
        chunk_start = self.data_segments[0]["start"]
        while i < len(self.data_segments):
            s_t.append(self.data_segments[i]["start"])
            l.append(self.data_segments[i]["loudness_start"])
            pitch_lists.append(self.data_segments[i]["pitches"])
            timbre_lists.append(self.data_segments[i]["timbre"])
            # If we've analyzed chunk_length seconds of data, and there is more than 2 segments remaining, break
            if self.data_segments[i]["start"] > chunk_start + chunk_length and i < len(self.data_segments) - 1:
                break
            i += 1
        chunk_end = self.data_segments[i]["start"] if i < len(self.data_segments) else self.data_segments[-1]["start"]

        # Discard data segments that were just analyzed
        self.data_segments = self.data_segments[i:]

        # Extract the next chunk_length seconds of useful beat data
        beat_times = []
        i = 0
        b_chunk_start = self.beats[0]["start"]
        while i < len(self.beats):
            beat_time = self.beats[i]["start"]
            beat_dur = self.beats[i]["duration"]
            beat_conf = self.beats[i]["confidence"]
            beat_times.append(beat_time)
            self.beat_info[beat_time] = (beat_dur, beat_conf, False)
            # If we've analyzed chunk_length seconds of data, and there is more than 2 beats remaining, break
            if self.beats[i]["start"] > b_chunk_start + chunk_length and i < len(self.beats) - 1:
                break
            i += 1
        b_chunk_end = self.beats[i]["start"] if i < len(self.beats) else self.beats[-1]["start"]

        # Discard beats that were just analyzed
        self.beats = self.beats[i:]

        # Perform data interpolation for loudness, pitch, timbre, and beat times
        start_times = np.array(s_t)
        loudnesses = np.array(l)
        interpolated_loudness_func = interp1d(start_times, loudnesses, kind='cubic', assume_sorted=True)
        interpolated_beats_func = interp1d(beat_times, beat_times, kind='previous', assume_sorted=True)
        interpolated_pitch_funcs, interpolated_timbre_funcs = [], []
        for i in range(12):
            # Create a separate interpolated pitch function for each of the 12 elements of the pitch vectors
            interpolated_pitch_funcs.append(
                interp1d(
                    start_times,
                    [pitch_list[i] if pitch_list[i] >= 0 else 0 for pitch_list in pitch_lists],
                    kind="linear",
                    assume_sorted=True
                )
            )
            # Create a separate interpolated timbre function for each of the 12 elements of the timbre vectors
            interpolated_timbre_funcs.append(
                interp1d(
                    start_times,
                    [timbre_list[i] for timbre_list in timbre_lists],
                    kind="linear",
                    assume_sorted=True
                )
            )

        # Add interpolated functions and their bounds to buffers for consumption by visualization thread
        self.buffer_lock.acquire()
        self.interpolated_loudness_buffer.append((chunk_start, chunk_end, interpolated_loudness_func))
        self.interpolated_beats_buffer.append((b_chunk_start, b_chunk_end, interpolated_beats_func))
        self.interpolated_pitch_buffer.append((chunk_start, chunk_end, interpolated_pitch_funcs))
        self.interpolated_timbre_buffer.append((chunk_start, chunk_end, interpolated_timbre_funcs))

        # Print information about the data chunk load that was just performed
        title = "--------------------DATA LOAD REPORT--------------------\n"
        data_seg = "Data segments remaining: {}.\n".format(len(self.data_segments))
        beats_left = "Beats remaining {}.\n".format(len(self.beats))
        beats = "Interpolated beats buffer size: {}.\n".format(len(self.interpolated_beats_buffer))
        loudness = "Interpolated loudness buffer size: {}.\n".format(len(self.interpolated_loudness_buffer))
        pitch = "Interpolated pitch buffer size: {}.\n".format(len(self.interpolated_pitch_buffer))
        timbre = "Interpolated timbre buffer size: {}.\n".format(len(self.interpolated_timbre_buffer))
        closer = "--------------------------------------------------------"
        self.buffer_lock.release()
        text = title + data_seg + beats_left + beats + loudness + pitch + timbre + closer
        print(SpotifyVisualizer._make_text_effect(text, ["blue"]))

    @staticmethod
    def _make_text_effect(text, text_effects):
        """"Applies text effects to text and returns it.

        Supported text effects:
            "green", "red", "blue", "bold"

        Args:
            text (str): the text to apply effects to.
            text_effects (list): a list of str, each str representing an effect to apply to the text.

        Returns:
            text (str) with effects applied.
        """
        effects = {
            "green": "\033[92m",
            "red": "\033[91m",
            "blue": "\033[94m",
            "bold": "\033[1m"
        }
        end_code = "\033[0m"
        msg_with_fx = ""
        for effect in text_effects:
            msg_with_fx += effects[effect]
        msg_with_fx += text
        msg_with_fx += end_code * len(text_effects)
        return msg_with_fx

    @staticmethod
    def _normalize_loudness(loudness, range_min=-54.0, range_max=-4.0):
        """Normalize a loudness value to the range specified.

        Args:
            loudness (float): the loudness value to normalize.
            range_min (float): the lower bound of the range.
            range_max (float): the upper bound of the range.

        Returns:
            the normalized loudness value (float between 0.0 and 1.0) for the specified range.
        """
        if loudness > range_max:
            return 1.0
        if loudness < range_min:
            return 0.0
        range_size = range_max - range_min
        return (loudness - range_min) / range_size

    def _push_visual_to_strip(self, loudness_func, pitch_funcs, timbre_funcs, beat_func, pos):
        """Displays a visual on the LED strip based on the loudness, pitches and timbre at current playback position.

        Args:
            loudness_func (interp1d): interpolated loudness function.
            pitch_funcs (list): a list of interpolated pitch functions (one pitch function for each major musical key).
            timbre_funcs (list): a list of interpolated timbre functions (one timbre function for each basis function).
            beat_func (interp1d): interpolated beat start time function.
        """
        # Normalize loudness
        norm_loudness = SpotifyVisualizer._normalize_loudness(loudness_func(pos))
        print("%f: %f" % (pos, norm_loudness))
        prev_beat_start = np.asscalar(beat_func(pos))
        beat_dur, beat_conf, should_visualize = self.beat_info[prev_beat_start]
        # print("Previous beat at {} with duration {} and confidence {}".format(prev_beat_start, beat_dur, beat_conf))

        # Change start color (base color) based on KMeans clustering if available
        if self.interpolated_label_func:
            colors = {
                0: (255, 0, 0),
                1: (0, 255, 0),
                2: (255, 0, 0)
            }
            label = self.interpolated_label_func(pos)
            self.start_color = colors[label]
            print("Cluster number: {}".format(label))

        # Determine how many pixels to light (growing from center of strip) based on loudness
        mid = self.num_pixels // 2
        length = int(self.num_pixels * norm_loudness)
        lower = mid - round(length / 2)
        upper = mid + round(length / 2)

        # Visualize beats of reasonable confidence if loudness is high (continue visualizing a beat if already started)
        brightness = 100
        if (beat_conf > 0.4 and norm_loudness > 0.9) or should_visualize:
            if not should_visualize:
                self.beat_info[prev_beat_start][2] = True
            time_elapsed = pos - prev_beat_start
            multiplier = np.sqrt(-1 * (time_elapsed / beat_dur)**2 + 1) if beat_dur != 0 else 0
            brightness = 10 + (90 * multiplier)

        # Segment strip into 12 zones (1 zone for each of the 12 pitch keys) and determine zone color by pitch strength
        for i in range(0, 12):
            pitch_strength = pitch_funcs[i](pos)
            if i in range(6):
                start = lower + (i * length // 12)
                end = lower + ((i + 1) * length // 12)
            else:
                start = upper - ((11 - i + 1) * length // 12)
                end = upper - ((11 - i) * length // 12)
            segment_len = end - start
            segment_mid = start + (segment_len // 2)

            # Set middle pixel to always be start_color (if odd number of pixels, segments don't cover middle pixel)
            start_r, start_g, start_b = self.start_color
            self.strip.set_pixel(mid, start_r, start_g, start_b, brightness)

            # Get the appropriate RGB color based on the current pitch zone and pitch strength
            r, g, b = self._calculate_zone_color(pitch_strength, i)

            # Fade the strength of the RGB values near the ends of the zone to produce a nice gradient effect
            for j in range(start, end + 1):
                color_strength = (1.0 + (j - start)) / (1.0 + (segment_mid - start))
                if color_strength > 1.0:
                    color_strength = 2.0 - color_strength
                faded_r, faded_g, faded_b = self._apply_gradient_fade(r, g, b, color_strength)
                self.strip.set_pixel(j, faded_r, faded_g, faded_b, brightness)

        # Make sure to clear ends of the strip that are not in use and update strip
        self.strip.fill(0, lower, 0, 0, 0, 0)
        self.strip.fill(upper, self.num_pixels, 0, 0, 0, 0)
        self.strip.show()

    def _reset(self):
        """Reset certain attributes to prepare to visualize a new track.
        """
        self.data_segments = []
        self.interpolated_label_func = None
        self.interpolated_loudness_buffer = []
        self.interpolated_pitch_buffer = []
        self.interpolated_timbre_buffer = []
        self.playback_pos = 0
        self.should_terminate = False
        self.strip.fill(0, self.num_pixels, 0, 0, 0, 0)
        self.strip.show()
        self.track = None
        self.track_duration = None
        self.track_id = None

    def _reset_track(self):
        """Pauses track and seeks to beginning.
        """
        text = "Starting track from beginning."
        print(SpotifyVisualizer._make_text_effect(text, ["green"]))
        if self.sp_gen.current_playback()["is_playing"]:
            self.sp_gen.pause_playback()
        self.sp_gen.seek_track(0)

    def _visualize(self, sample_rate=0.03):
        """Starts playback on Spotify user's account (if paused) and visualizes the current track.

        Args:
            sample_rate (float): how long to wait (in seconds) between each sample.
        """
        pos = self.playback_pos
        loudness_func, pitch_funcs, timbre_funcs, beat_func = self._get_buffers_for_pos(pos)

        if not self.sp_vis.current_playback()["is_playing"]:
            self.sp_vis.start_playback()

        # Visualize until end of track
        while pos <= self.track_duration:
            start = time.perf_counter()
            if self.should_terminate:
                text = "Killing visualization thread."
                print(SpotifyVisualizer._make_text_effect(text, ["red", "bold"]))
                exit(0)

            try:
                pos = self.playback_pos
                self._push_visual_to_strip(loudness_func, pitch_funcs, timbre_funcs, beat_func, pos)
            # If pitch or loudness value out of range, find the interpolated functions for the current position
            except:
                funcs = self._get_buffers_for_pos(pos)
                if funcs:
                    loudness_func, pitch_funcs, timbre_funcs, beat_func = funcs
                else:
                    text = "Killing visualization thread."
                    print(SpotifyVisualizer._make_text_effect(text, ["red", "bold"]))
                    exit(0)

            self.pos_lock.acquire()
            self.playback_pos += sample_rate
            self.pos_lock.release()
            end = time.perf_counter()
            # Account for time used to create visualization
            diff = sample_rate - (end - start)
            time.sleep(diff if diff > 0 else 0)


if __name__ == "__main__":
    # Instantiate an instance of SpotifyVisualizer and start visualization
    visualizer = SpotifyVisualizer(240)
    visualizer.visualize()
