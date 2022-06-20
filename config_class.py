import os
import configparser


class Config():
    def __init__(self, cfg_relative_path='config.ini'):
        self.cfg_path = cfg_relative_path
        self.root_path = os.path.dirname(os.path.abspath(__file__))
        self.full_path = os.path.join(self.root_path, self.cfg_path)

    # READ CONFIG VALUES AND STORE THEM
    def init_config(self):
        # Check if config file exists. If not -> create new file.
        if not os.path.exists(self.full_path) or os.stat(self.full_path).st_size == 0:
            raise SystemError('Config file is not found at {}'.format(self.full_path))

        config = configparser.ConfigParser()
        config.read(self.full_path)
        return config


config = Config()
CONFIG = config.init_config()
API_PROFILES = CONFIG['api_credentials']
