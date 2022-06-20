# Used this tutorial to setup AWS https://towardsdatascience.com/how-to-upload-and-download-files-from-aws-s3-using-python-2022-4c9b787b15f2
import os

import boto3

from config_class import API_PROFILES


class AWS_S3(object):
    '''Minimal Amazon AWS class to communicate with S3 data storage service.
       Currently supports only download and upload commands.
       In context of Telegram bot used to store crypto_info.pickle online, which is a dict with Coinmarketcap coin meta infos.
    '''
    AWS_BUCKET_NAME = API_PROFILES['AWS_BUCKET_NAME']
    AWS_ACCESS_KEY_ID = API_PROFILES['AWS_ACCESS_KEY_ID']
    AWS_SERVER_SECRET_KEY = API_PROFILES['AWS_SERVER_SECRET_KEY']
    REGION = API_PROFILES['REGION']

    def __init__(self):
        self.SESSION = boto3.Session(
            aws_access_key_id=self.AWS_ACCESS_KEY_ID,
            aws_secret_access_key=self.AWS_SERVER_SECRET_KEY,
            region_name=self.REGION,
        )
        self.S3_CLIENT = self.SESSION.client('s3')

    def download_file(self, aws_filename, local_full_file_path):
        self.S3_CLIENT.download_file(Bucket=self.AWS_BUCKET_NAME,
                                     Key=aws_filename,
                                     Filename=local_full_file_path)

    def upload_file(self, aws_filename, local_full_file_path):
        self.S3_CLIENT.upload_file(Filename=local_full_file_path,
                                   Bucket=self.AWS_BUCKET_NAME,
                                   Key=aws_filename)


# Example. NOTE: download_file() raises no errors. If 'PICKLE_NAME' is uploaded to AWS S3, it will download it.
if __name__ == '__main__':
    try:
        PICKLE_NAME = 'crypto_info.pickle'
        DIR_PATH = os.path.dirname(os.path.abspath(__file__))
        PICKLE_ABS_PATH = os.path.join(DIR_PATH, PICKLE_NAME)
        BUCKET_NAME = 'telegram-bot-5051'
        S3 = AWS_S3()
        S3.download_file(PICKLE_NAME, PICKLE_ABS_PATH, BUCKET_NAME)
    except KeyboardInterrupt:
        quit()
