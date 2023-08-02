import json
import os

DEFAULT_SETTINGS = {
    'LIGHTS_ON_AFTER_START_UP': True,
    'BASE_RGB': (255, 255, 255),
    'GIT_BRANCH': 'master',
    'GIT_COMMIT_ID': 'dd1490f'
}

class SettingsHandler():
    def __init__(self, path):
        self.settings_path = path

        if not os.path.exists(path):
            os.mknod(path)
            self._write_settings(DEFAULT_SETTINGS)
    
    def _read_settings(self):
        data = None
        with open(self.settings_path, 'r') as json_file:
            data = json.load(json_file)
        return data

    def _write_settings(self, data):
        if data is None:
            data = {}
        
        with open(self.settings_path, 'w') as json_file:
            json.dump(data, json_file, indent=4)
        
    def update_base_color(self, r=None, g=None, b=None):
        rgb_arr = [r, g, b]
        settings = self._read_settings()
        rgb_arr = [color if color is not None else settings['BASE_RGB'][i] for i, color in enumerate(rgb_arr)]
        settings['BASE_RGB'] = rgb_arr
        self._write_settings(settings)

    def get_base_color(self):
        settings = self._read_settings()
        return tuple(settings['BASE_RGB'])

    def update_git_branch(self, branch=None):
        settings = self._read_settings()
        settings['GIT_BRANCH'] = branch
        self._write_settings(settings)

    def get_git_branch(self):
        settings = self._read_settings()
        return tuple(settings['GIT_BRANCH'])
    
    def update_git_commit(self, commit=None):
        settings = self._read_settings()
        settings['GIT_COMMIT'] = commit
        self._write_settings(settings)

    def get_git_commit(self):
        settings = self._read_settings()
        return tuple(settings['GIT_COMMIT'])

    def update_default_behaviour(self, truth_value):
        settings = self._read_settings()
        settings['DEFAULT_BEHAVIOUR'] = truth_value
        self._write_settings(settings)

    def get_default_behaviour(self):
        settings = self._read_settings()
        return settings['DEFAULT_BEHAVIOUR']
    
    def add_animation(self, animation_name):
        settings = self._read_settings()
        enabled_animations = settings['ANIMATIONS_LIST']
        if animation_name in enabled_animations:
            return
        
        enabled_animations.append(animation_name)
        self._write_settings(settings)

    def remove_animation(self, animation_name):
        settings = self._read_settings()
        enabled_animations = settings['ANIMATIONS_LIST']
        if animation_name not in enabled_animations:
            return
        
        enabled_animations.remove(animation_name)
        self._write_settings(settings)

    def get_animations(self):
        return self._read_settings()['ANIMATIONS_LIST']
    
    def reset_settings(self):
        self._write_settings(DEFAULT_SETTINGS)