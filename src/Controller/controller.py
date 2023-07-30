from src.Controller.settings_handler import SettingsHandler
from src.SpotifyLights.light_manager import manage
from src.Controller.rest_api import API
from src.Files.credentials import USERNAME, SPOTIPY_CLIENT_ID, SPOTIPY_CLIENT_SECRET, SPOTIPY_REDIRECT_URI
from spotipy.oauth2 import SpotifyOAuth
import threading
from queue import Queue
import json
permission_scopes = "user-modify-playback-state user-read-currently-playing user-read-playback-state"

class Controller:
    def __init__(self):
        self.settings_handler = SettingsHandler("./src/Files/settings.json")
        self.oauth_handler = SpotifyOAuth(username=USERNAME, client_id=SPOTIPY_CLIENT_ID, client_secret=SPOTIPY_CLIENT_SECRET, redirect_uri=SPOTIPY_REDIRECT_URI, 
                         scope=permission_scopes, cache_path=f"./src/Files/.cache-{USERNAME}", open_browser=False)
        self.authenticated = False

        self.api_communicaton_queue = Queue()
        self.api_kill_sentinel = object()
        self.api = API(self.api_communicaton_queue, self.api_kill_sentinel)
        self.api_thread = None


        self.spotify_lights_thread = None
        self.controller_to_lights_queue = Queue() # a queue so the controller can pass messages to spotify lights manager thread
        self.light_to_controller_queue = Queue() # a queue so spotify lights manager thread can pass messages to controller
        self.spotify_lights_kill_sentinel = object()

        self.current_command = None

    def _token_is_valid(self):
        try:
            with open(f"./src/Files/.cache-{USERNAME}", 'r') as token_cache:
                token_info = json.load(token_cache)
                valid_token = self.oauth_handler.validate_token(token_info)
                if valid_token:
                    return True
                else:
                    return False
        except:
            return False
        
    def run(self):
        self.authenticated = self._token_is_valid()
        
        self.api_thread = threading.Thread(target=self.api.run, name="rest_api_thread")
        self.api_thread.start()
        
        while True:
            self.authenticated = self._token_is_valid() # validate token to ensure connection to spotify is not lost
            if not self.api_communicaton_queue.empty():
                command = self.api_communicaton_queue.get()
                if command['COMMAND'] == 'SWITCH_SPOTIFY_LIGHTS_ON_OFF':
                    if self._spotify_lights_are_running():
                        self._kill_spotify_lights()
                        self.current_command = "SPOTIFY_LIGHTS_OFF"
                    else:
                        self._start_spotify_lights()
                        self.current_command = "SPOTIFY_LIGHTS_ON"
                    self.api_communicaton_queue.task_done()

            # default behaviour should only be triggered if there is no overriding command in self.current_command
            if not self.current_command:
                if self.settings_handler.get_default_behaviour() == "SPOTIFY_LIGHTS" and self.authenticated and not self._spotify_lights_are_running():
                    self._start_spotify_lights()
                elif self.settings_handler.get_default_behaviour() == "LIGHTS_OFF" and self._spotify_lights_are_running():
                    self._kill_spotify_lights()


    def _start_spotify_lights(self):
        self.controller_to_lights_queue = Queue() # a queue so the controller can pass messages to spotify lights manager thread
        self.light_to_controller_queue = Queue() # a queue so spotify lights manager thread can pass messages to controller
        self.spotify_lights_thread = threading.Thread(target=manage, name="spotify_lights_thread", args=(False, self.settings_handler.get_base_color(), self.oauth_handler,
                                                                                                  self.controller_to_lights_queue, self.light_to_controller_queue, self.spotify_lights_kill_sentinel))
        self.spotify_lights_thread.start()

    def _kill_spotify_lights(self):
        self.controller_to_lights_queue.put(self.spotify_lights_kill_sentinel)
        self.controller_to_lights_queue.join() # wait for spotify lights to mark command as completed
        self.spotify_lights_thread.join() 

        self.controller_to_lights_queue = Queue() # clear the queues if there were any messages waiting
        self.light_to_controller_queue = Queue()

    def _spotify_lights_are_running(self):
        return self.spotify_lights_thread and self.spotify_lights_thread.is_alive()
            