import os
import time
import re
import datetime
from apscheduler.schedulers.background import BackgroundScheduler  # Scheduling background tasks with defined frequency
import pickle  # Data structures storing: dicts with crypto info
from math import ceil  # Rounding up numbers for a print in CMCPRices.getCryptoPrice()
from typing import Union  # Function argument annotations

import emoji  # Converts text like :waving_hand: to its visual representation (real emoji).
from coinmarketcapapi import CoinMarketCapAPI, CoinMarketCapAPIError  # 3rd party 1:1 wrapper to CoinMarketCap API

from aws_s3 import AWS_S3  # Our custom made Amazon AWS S3 client. Has only two functions: download/upload file.
from config_class import API_PROFILES


# When testing/running program locally, we don't want to redownload the same crypto_info.pickle from AWS on every run -> we already have file stored locally.
DEBUG_DONT_USE_AWS = True

EMOJIS = {
    "hello": emoji.emojize(':waving_hand:'),
    "shrug": emoji.emojize(':person_shrugging:'),
    "rocket": emoji.emojize(':rocket:'),
    "heart": emoji.emojize(':red_heart:'),
    "globe": emoji.emojize(':globe_showing_Americas:'),
    "thumbs_up": emoji.emojize(':thumbs_up:'),
    "thumbs_down": emoji.emojize(':thumbs_down:'),
    "zzz": emoji.emojize(':zzz:'),
    "detective": emoji.emojize(':detective:'),
    "red_triangle": emoji.emojize(':red_triangle_pointed_down:'),
    "tree": emoji.emojize(':deciduous_tree:'),
}


class CMCPrices(object):
    ''' A custom class that communicates with CoinmarketCap API through another python wrapper "CoinMarketCapAPI", which is 1:1 wrapper for CMC API.
        Uses another custom class AWS_S3 to download/upload data to Amazon cloud storage.
        '''

    COINMARKETCAP_API_KEY = API_PROFILES['COINMARKETCAP_API_KEY']
    COINMARKETCAP_API_KEY_2 = API_PROFILES['COINMARKETCAP_API_KEY_2']
    if COINMARKETCAP_API_KEY_2 == 'None':  # HACK
        COINMARKETCAP_API_KEY_2 = None

    # These vars are used to handle credit counting and account switching when active CMC API has ran out of credits.
    ALL_API_KEYS = [COINMARKETCAP_API_KEY, COINMARKETCAP_API_KEY_2]
    ACTIVE_API_KEY = COINMARKETCAP_API_KEY
    OUT_OF_ALL_CREDITS = False

    RETRY_REQUEST_SLEEP = 3  # seconds. If first request fails for any reason, how long to sleep before attempting second try?

    DIR_PATH = os.path.dirname(os.path.abspath(__file__))

    # Names for .pickle files to store data in
    CRYPTO_SYMBOLS_PICKLE_NAME = 'crypto_symbols.pickle'
    CRYPTO_SLUG_PICKLE_NAME = 'crypto_slugs.pickle'
    FIAT_SYMBOLS_PICKLE_NAME = 'fiat_symbols.pickle'
    CRYPTO_INFO_PICKLE_NAME = 'crypto_info.pickle'
    CRYPTO_INFO_PICKLE_PATH = os.path.join(DIR_PATH, CRYPTO_INFO_PICKLE_NAME)

    TELEGRAM_MSG_CHAR_LIMIT = 4050  # Used to divide long message in PrintSupportedCryptos() into chunks.

    REUPLOAD_DIFFERENCE = 10  # How many new crypto info keys must be added into self.CRYPTO_INFO dict during this session to trigger amazon upload.

    CMC_URL = 'https://coinmarketcap.com/currencies/'

    def __init__(self):
        self.CMC = CoinMarketCapAPI(self.ACTIVE_API_KEY)  # from coinmarketcapapi import CoinMarketCapAPI

        self.AWS = AWS_S3()

        # Attempt to load crypto/fiat symbols from pre-saved pickle files. If those do not exist or too old -> request new data
        self.CRYPTO_MAP = self.load_symbols_from_pickle(self.CRYPTO_SYMBOLS_PICKLE_NAME)
        self.SLUG_MAP = self.load_symbols_from_pickle(self.CRYPTO_SLUG_PICKLE_NAME)
        if self.CRYPTO_MAP is None or self.SLUG_MAP is None:
            self.CRYPTO_MAP, self.SLUG_MAP = self.get_crypto_symbols_and_slugs(save_pickle=True)  # Pull once a list of supported crypto tokens on CoinmarketCap
        self.FIAT_MAP = self.load_symbols_from_pickle(self.FIAT_SYMBOLS_PICKLE_NAME)
        if self.FIAT_MAP is None:
            self.FIAT_MAP = self.get_fiat_map(save_pickle=True)  # Pull once a list of supported fiat currencies on CoinmarketCap

        try:
            if DEBUG_DONT_USE_AWS is False:
                self.AWS.download_file(self.CRYPTO_INFO_PICKLE_NAME, self.CRYPTO_INFO_PICKLE_PATH)
                print('Downloading "{}" from AWS. Time sleep for 5 seconds.'.format(self.CRYPTO_INFO_PICKLE_NAME))
                time.sleep(5)
            self.CRYPTO_INFO = self.load_symbols_from_pickle(self.CRYPTO_INFO_PICKLE_NAME, expiration_hours=None)  # Once per two weeks
        except Exception as e:
            print(f'Failed to either download from AWS or load file frm pickle.{e}')
            self.CRYPTO_INFO = None

        if self.CRYPTO_INFO is not None:
            self.NUMBER_OF_SAVED_CRYPTO_INFO = len(self.CRYPTO_INFO)
            print('Number of crypto infos loaded into {0} = {1}'.format(self.CRYPTO_INFO_PICKLE_NAME, self.NUMBER_OF_SAVED_CRYPTO_INFO))

        scheduler = BackgroundScheduler(timezone="Europe/Berlin")
        scheduler.add_job(self.api_key_scheduled_check, 'cron', minute='*/10')
        scheduler.add_job(self.aws_crypto_info_check, 'cron', minute='0-59')
        scheduler.start()

    def getCryptoPrice(self, symbol: str, currency: str = 'USD'):
        ''' Main function to get a price quote on a crypto token.
            Checks whether crypto symbol exists in a list stored in CRYPTO_MAP (thats initialized in class __init__() function.
            If symbol exists, it will look it up. If not -> will return a message saying "Token not found".
            Does the same process above for specified fiat currencies. Uses "USD" by default. Case insensitive.
            Example call: getCryptoPrice('btc', 'EUR')
            Returns a nice print message formated for a telegram chat window (using Markdown syntax).
        '''
        if self.OUT_OF_ALL_CREDITS is True:
            msg = 'Sorry, I am out of mana! Come back soon!\n_(reached API call limit)_'
            return msg, False

        return_status = ''  # will be appended with status messages that can occur in this function.
        cryptolist_exists, fiatlist_exists = True, True
        if self.CRYPTO_MAP is None:
            print('Crypto map not found. Doing blind query')
            cryptolist_exists = False
        else:
            crypto_map = [x.lower() for x in self.CRYPTO_MAP]  # Storing a copy in lower casing.
        if self.FIAT_MAP is None:
            print('FIAT map not found. Doing blind query')
            fiatlist_exists = False
        else:
            fiat_map = [x.lower() for x in self.FIAT_MAP]  # Storing a copy in lower casing.

        if cryptolist_exists is True:
            if symbol.lower() not in crypto_map:
                return_status += 'Crypto token not found or misspelled.'
                return None, return_status

        project_url = ''
        if self.CRYPTO_INFO is not None:
            symbol_uppercased = symbol.upper()
            if symbol_uppercased in self.CRYPTO_INFO:
                project_url = self.CRYPTO_INFO[symbol_uppercased]['urls']['website'][0]
                # project_logo_url = self.CRYPTO_INFO[symbol_uppercased]['logo']
            else:
                token_info = self.get_crypto_info([symbol_uppercased], save_pickle=True)
                if token_info is not None:
                    project_url = token_info[symbol_uppercased]['urls']['website'][0]
                    # project_logo_url = token_info[symbol_uppercased]['logo']

        switched_to_default_currency = False
        if currency.lower() != 'usd':
            if fiatlist_exists is True:
                if currency.lower() not in fiat_map:
                    print('{0} is not in fiat map...'.format(currency))
                    def_currency = 'USD'
                    old_currency = currency  # for printing in the end
                    return_status += f'No currency "{currency}" was found. Used "{def_currency}" by default.'
                    currency = def_currency
                    switched_to_default_currency = True

        currency = currency.upper()
        data_quote, error = self.get_cryptocurrency_quote(symbol=symbol, currency=currency)
        if data_quote is None:
            return_status += str(error)
            return None, return_status
        tmp_crypto_data = data_quote.data[symbol.upper()]
        data = {
            'name': tmp_crypto_data['name'],
            'symbol': tmp_crypto_data['symbol'],
            'currency': currency,
            'price': self.round_nonzero(tmp_crypto_data['quote'][currency]['price'], digits_to_keep=2),
            'market_cap': self.round_nonzero(tmp_crypto_data['quote'][currency]['market_cap'], digits_to_keep=2),
            'volume_24h': self.round_nonzero(tmp_crypto_data['quote'][currency]['volume_24h'], digits_to_keep=2),
            'percent_change_24h': self.round_nonzero(tmp_crypto_data['quote'][currency]['percent_change_24h'], digits_to_keep=2),
            'last_updated': tmp_crypto_data['quote'][currency]['last_updated'][:-5].replace('T', ' ').split()[1] + ' UTC+0'
        }
        if switched_to_default_currency is True:
            currency_status_line = '_Currency {0} was not found. Used default {1} instead._\n'.format(old_currency, def_currency)
        else:
            currency_status_line = ''
        # NOTE: One day re-code it to fit 120-160 lines limit...
        # header = f"*Crypto Price Finder BOT!* {EMOJIS['detective']} \n"
        slug_list_id = self.CRYPTO_MAP.index(data['symbol'])

        project_url_string = '         [Project page]({})\n\n'.format(project_url) if len(project_url) > 0 else '\n\n'
        name = f"\n[{data['name']} ({data['symbol']})]({self.CMC_URL + self.SLUG_MAP[slug_list_id]})" + project_url_string
        price = f"Price:                  *{data['price']}* {data['currency']}\n"
        market_cap = f"Market Cap:     {ceil(float(data['market_cap'])):,} {data['currency']}\n"
        volume = f"Volume 24h:    {ceil(float(data['volume_24h'])):,} {data['currency']}\n"
        last_updated = f"Last Updated: {data['last_updated']}\n\n"
        if float(data['percent_change_24h']) >= 50:
            emoji_status = f"{EMOJIS['rocket']}{EMOJIS['rocket']}{EMOJIS['rocket']}"
        elif float(data['percent_change_24h']) >= 25:
            emoji_status = f"{EMOJIS['rocket']}{EMOJIS['rocket']}"
        elif float(data['percent_change_24h']) >= 15:
            emoji_status = f"{EMOJIS['rocket']}"
        elif float(data['percent_change_24h']) >= 2:
            emoji_status = f"{EMOJIS['thumbs_up']}"
        elif float(data['percent_change_24h']) >= -2 and float(data['percent_change_24h']) <= 2:
            emoji_status = f"{EMOJIS['zzz']}"
        elif float(data['percent_change_24h']) <= -50:
            emoji_status = f"{EMOJIS['red_triangle']}{EMOJIS['red_triangle']}{EMOJIS['red_triangle']}"
        elif float(data['percent_change_24h']) <= -25:
            emoji_status = f"{EMOJIS['red_triangle']}{EMOJIS['red_triangle']}"
        elif float(data['percent_change_24h']) <= -15:
            emoji_status = f"{EMOJIS['red_triangle']}"
        elif float(data['percent_change_24h']) <= -2:
            emoji_status = f"{EMOJIS['thumbs_down']}"
        change = f"Pct.Ch. 24h:      {data['percent_change_24h']}% {emoji_status}\n"
        powered_by = "[Powered by @crypto_price_finder_bot](https://t.me/crypto_price_finder_bot)" + f"{EMOJIS['tree']}"
        output_string = f"{currency_status_line}{name}{price}{market_cap}{volume}{change}{last_updated}{powered_by}"
        # nice_output_msg += '\n_If you like the bot, consider donating with_ */donate* _command. Cheers!_'
        if len(return_status) == 0:
            return_status = 'Token has been found!'
        return output_string, return_status

    def PrintSupportedCryptos(self) -> tuple[list, bool]:
        ''' Returns a long string with crypto symbols, supported in CoinMarketCap API. Currently the number is 10000.
            NOTE: Something not done with pagination in request, returns only 10k symbols, but has more. Can be fixed but didnt bother...
            Slices big string into smaller chunks, storing in a list.
        '''
        if self.OUT_OF_ALL_CREDITS is True:
            msg = 'Sorry, I am out of mana! Come back soon!\n_(reached API call limit)_'
            status = False
            return msg, status

        assert self.CRYPTO_MAP is not None, 'Supported crypto tokens list is not available...'
        str_1 = '{0} The following {1} crypto tokens are supported:\n'.format(EMOJIS['globe'], len(self.CRYPTO_MAP))
        str_2 = " ".join(map(str, self.CRYPTO_MAP))
        # Line below chops a large string into equal portions of 'self.TELEGRAM_MSG_CHAR_LIMIT' char length.
        msg = [str_2[i:i + self.TELEGRAM_MSG_CHAR_LIMIT] for i in range(0, len(str_2), self.TELEGRAM_MSG_CHAR_LIMIT)]
        msg.insert(0, str_1)
        msg.append('Sorry for spam! Above are {0} crypto tokens!\n'.format(len(self.CRYPTO_MAP)))
        msg.append('Use Ctrl+F to find the one you need! If you are on the phone, well... Good luck searching!')
        return msg, True

    def PrintSupportedFiats(self) -> str:
        '''Returns a string with fiat currency symbols supported in CoinMarketCap API.'''
        if self.OUT_OF_ALL_CREDITS is True:
            msg = 'Sorry, I am out of mana! Come back soon!\n_(reached API call limit)_'
            return msg, False

        assert self.FIAT_MAP is not None, 'Supported fiat currency list is not available...'
        str_1 = '{0} The following {1} fiat currencies are supported:\n'.format(EMOJIS['globe'], len(self.FIAT_MAP))
        str_2 = " ".join(map(str, self.FIAT_MAP))
        return str_1 + str_2, True

    def PrintKeyInfo(self) -> str:
        '''A utility function to print CoinMarketCap API usage info when Telegram user uses /secret command.'''
        data_quote, error = self.get_key_info()
        if data_quote is None:
            return None, error
        usage = data_quote.data['usage']
        str_1 = 'You found the secret. Congratulations... I guess. {}\n'.format(EMOJIS['shrug'])
        str_2 = 'Credits used [minute]: {0} / {1}\n'.format(
            usage['current_minute']['requests_made'], usage['current_minute']['requests_left'] + usage['current_minute']['requests_made'])
        str_3 = 'Credits used [day]: {0} / {1}\n'.format(
            usage['current_day']['credits_used'], usage['current_day']['credits_left'] + usage['current_day']['credits_used'])
        str_4 = 'Credits used [month]: {0} / {1}'.format(
            usage['current_month']['credits_used'], usage['current_month']['credits_left'] + usage['current_month']['credits_used'])
        result_msg = f"{str_1}{str_2}{str_3}{str_4}"
        return result_msg

    def get_key_info(self) -> tuple[dict, str]:
        '''Request to get CMC API usage info using 3rd party CMC wrapper.'''
        try:
            data_quote = self.CMC.key_info()
            status = self.api_status_handler(data_quote.status)
        except CoinMarketCapAPIError:
            time.sleep(self.RETRY_REQUEST_SLEEP)
            try:
                data_quote = self.CMC.key_info()
                status = self.api_status_handler(data_quote.status)
            except CoinMarketCapAPIError as e:
                return None, e
        return data_quote, status

    def get_crypto_symbols_and_slugs(self, save_pickle: bool = False) -> list or None:
        '''Request to get crypto symbols and "slugs"(url component) of all cryptos supported in CMC.
           Request uses 3rd party CMC wrapper. Can store data in a .pickle file is "save_pickle=True
        '''
        try:
            c_map = self.CMC.cryptocurrency_map()
        except CoinMarketCapAPIError:
            time.sleep(self.RETRY_REQUEST_SLEEP)
            try:
                c_map = self.CMC.cryptocurrency_map()
            except CoinMarketCapAPIError as e:
                print(e)
                return None
        symbols_list = []
        slug_list = []  # nicknames that CMC uses for cryptos. Used in getting crypto URL.
        for item in c_map.data:
            symbols_list.append(item['symbol'])
            slug_list.append(item['slug'])

        if save_pickle is True:
            if '.pickle' not in self.CRYPTO_SYMBOLS_PICKLE_NAME:  # if file name provided contains no extension -> add it.
                symbols_pickle_file_name = self.CRYPTO_SYMBOLS_PICKLE_NAME + '.pickle'
                slugs_pickle_file_name = self.CRYPTO_SLUG_PICKLE_NAME + '.pickle'
            else:
                symbols_pickle_file_name = self.CRYPTO_SYMBOLS_PICKLE_NAME
                slugs_pickle_file_name = self.CRYPTO_SLUG_PICKLE_NAME
            try:
                with open(os.path.join(self.DIR_PATH, symbols_pickle_file_name), "wb") as f:
                    pickle.dump(symbols_list, f)
                with open(os.path.join(self.DIR_PATH, slugs_pickle_file_name), "wb") as f:
                    pickle.dump(slug_list, f)
            except Exception as e:
                print('PICKLE ERROR: {}'.format(e))
        print('Loaded fresh list of crypto tokens listed on CoinmarketCap.')
        return [symbols_list, slug_list]

    def get_fiat_map(self, save_pickle: bool = False) -> list or None:
        '''Request to get fiat currency symbols supported in CMC.
           Request uses 3rd party CMC wrapper. Can store data in a .pickle file is "save_pickle=True
        '''
        try:
            f_map = self.CMC.fiat_map()
        except CoinMarketCapAPIError:
            time.sleep(self.RETRY_REQUEST_SLEEP)
            try:
                f_map = self.CMC.fiat_map()
            except CoinMarketCapAPIError as e:
                print(e)
                return None
        symbols_list = []
        for item in f_map.data:
            symbols_list.append(item['symbol'])

        if save_pickle is True:
            if '.pickle' not in self.FIAT_SYMBOLS_PICKLE_NAME:  # if file name provided contains no extension -> add it.
                pickle_file_name = self.FIAT_SYMBOLS_PICKLE_NAME + '.pickle'
            else:
                pickle_file_name = self.FIAT_SYMBOLS_PICKLE_NAME
            try:
                with open(os.path.join(self.DIR_PATH, pickle_file_name), "wb") as f:
                    pickle.dump(symbols_list, f)
            except Exception as e:
                print('PICKLE ERROR: {}'.format(e))
        print('Loaded fresh list of fiat currencies available on CoinmarketCap.')
        return symbols_list

    def get_crypto_info(self, symbols_list, save_pickle: bool = False) -> list or None:
        '''Request to get a crypto currency info. Input supports both a single symbol in string or a list of strings with many symbols.
           Request uses 3rd party CMC wrapper. Can store data in a .pickle file is "save_pickle=True
        '''
        if not isinstance(symbols_list, list) and isinstance(symbols_list, str):
            symbols_list = [symbols_list]
        elif isinstance(symbols_list, list):
            pass
        else:
            raise TypeError('Wrong input for argument "symbols_list" in CMCPrices.get_crypto_info() method. Must be string or list')
        crypto_list_string = ''
        for x in range(len(symbols_list)):
            crypto_list_string += symbols_list[x] + ','
        crypto_list_string = crypto_list_string[:-1]

        try:
            c_info = self.CMC.cryptocurrency_info(symbol=crypto_list_string)
        except CoinMarketCapAPIError:
            time.sleep(self.RETRY_REQUEST_SLEEP)
            try:
                c_info = self.CMC.cryptocurrency_info(symbol=crypto_list_string)
            except CoinMarketCapAPIError as e:
                print(e)
                return None

        if save_pickle is True:
            if '.pickle' not in self.CRYPTO_INFO_PICKLE_NAME:
                pickle_file_name = self.CRYPTO_INFO_PICKLE_NAME + '.pickle'
            else:
                pickle_file_name = self.CRYPTO_INFO_PICKLE_NAME

            full_path = os.path.join(self.DIR_PATH, pickle_file_name)  # NOTE: Only looks in directory where this script is running

            if not os.path.exists(full_path):
                print(f'{pickle_file_name} doesnt exist. Creating new one.')
                try:
                    with open(os.path.join(full_path), "wb") as f:
                        pickle.dump(c_info.data, f)
                        print('Loaded fresh crypto info package ({} different crypto tokens) available on CoinmarketCap.'.format(len(c_info.data)))
                except Exception as e:
                    print('PICKLE ERROR: {}'.format(e))

            else:
                try:
                    with open(full_path, "rb") as f:
                        pickle_data = pickle.load(f)
                except Exception as e:
                    print('PICKLE ERROR: {}'.format(e))
                for token in symbols_list:
                    pickle_data[token] = c_info.data[token]
                try:
                    with open(os.path.join(full_path), "wb") as f:
                        pickle.dump(pickle_data, f)
                        print('Updated existing crypto_info pickle (total {0} tokens) with new token(s): {1}.'.format(len(pickle_data), symbols_list))
                except Exception as e:
                    print('PICKLE ERROR: {}'.format(e))
            self.CRYPTO_INFO = self.load_symbols_from_pickle(self.CRYPTO_INFO_PICKLE_NAME, expiration_hours=None)
        return c_info.data

    def get_cryptocurrency_quote(self, symbol: str, currency: str = 'USD') -> dict:
        '''Request to get a crypto currency price quote.
           Request uses 3rd party CMC wrapper. Can store data in a .pickle file is "save_pickle=True
        '''
        try:
            data_quote = self.CMC.cryptocurrency_quotes_latest(symbol=symbol, convert=currency)
            status = self.api_status_handler(data_quote.status)
        except CoinMarketCapAPIError:
            time.sleep(self.RETRY_REQUEST_SLEEP)
            try:
                data_quote = self.CMC.cryptocurrency_quotes_latest(symbol=symbol, convert=currency)
                status = self.api_status_handler(data_quote.status)
            except CoinMarketCapAPIError as e:
                return None, e
        return data_quote, status

    def api_status_handler(self, status_dict: dict) -> str:
        ''' Takes in status part of Coinmarketcap API request return and handles errors.'''
        if status_dict['error_code'] == 0:
            return 'HTTP Status: 200 Successful'
        elif status_dict['error_code'] == 1001:
            return 'HTTP Status: 401. Error Code: 1001. ' + status_dict['error_message']
        elif status_dict['error_code'] == 1002:
            return 'HTTP Status: 401. Error Code: 1002. ' + status_dict['error_message']
        elif status_dict['error_code'] == 1003:
            return 'HTTP Status: 402. Error Code: 1003. ' + status_dict['error_message']
        elif status_dict['error_code'] == 1004:
            return 'HTTP Status: 402. Error Code: 1004. ' + status_dict['error_message']
        elif status_dict['error_code'] == 1005:
            return 'HTTP Status: 403. Error Code: 1005. ' + status_dict['error_message']
        elif status_dict['error_code'] == 1006:
            return 'HTTP Status: 403. Error Code: 1006. ' + status_dict['error_message']
        elif status_dict['error_code'] == 1007:
            return 'HTTP Status: 403. Error Code: 1007. ' + status_dict['error_message']
        elif status_dict['error_code'] == 1008:
            return 'HTTP Status: 429. Error Code: 1008. ' + status_dict['error_message']
        elif status_dict['error_code'] == 1009:
            return 'HTTP Status: 429. Error Code: 1009. ' + status_dict['error_message']
        elif status_dict['error_code'] == 1010:
            return 'HTTP Status: 429. Error Code: 1010. ' + status_dict['error_message']
        elif status_dict['error_code'] == 1011:
            return 'HTTP Status: 429. Error Code: 1011. ' + status_dict['error_message']

    def api_key_scheduled_check(self, day_threshold: float = 0.99, month_threshold: float = 0.99) -> None:
        '''Checks API usage and tries to switch to another API key stored in  "ALL_API_KEYS" class variable.
           The switch happens if credits used >= "day(month)_threshold" * credits total. Credits info obtained from get_key_info() method.
           Flips OUT_OF_ALL_CREDITS to True if no credits left in all keys... Will print the same "Out of mana message. Wait X time" to users.
        '''
        # Just putting currently used API key to the beginning of the list.
        self.ALL_API_KEYS.remove(self.ACTIVE_API_KEY)
        self.ALL_API_KEYS.insert(0, self.ACTIVE_API_KEY)

        for key in self.ALL_API_KEYS:
            if key is not None:
                current_key_good = False
                if key == self.ACTIVE_API_KEY:  # If key is point at the currently used api key -> Don't do anything
                    current_key_good = True
                else:  # If we switch the keys, we need to reload self.CMC class initialized in __init__()
                    self.ACTIVE_API_KEY = key
                    self.CMC = CoinMarketCapAPI(self.ACTIVE_API_KEY)
                data_quote, error = self.get_key_info()
                if data_quote is None:
                    print(error)
                usage = data_quote.data['usage']
                plan = data_quote.data['plan']
                if not (usage['current_day']['credits_used'] >= plan['credit_limit_daily'] * day_threshold) and \
                   not (usage['current_month']['credits_used'] >= plan['credit_limit_monthly'] * month_threshold):
                    self.OUT_OF_ALL_CREDITS = False
                    if current_key_good is True:
                        pass
                    else:
                        print('Switched to another API key with available credits.')
                    return

        # If the code got here -> no credits left on any of API keys.
        # Flip "self.OUT_OF_ALL_CREDITS" that will block all requests and just print "Out of mana" for users in Telegram.
        # NOTE: Add later -> "Come back in X time when I will regenerate mana and work again."
        print('Out of all credits...')
        self.OUT_OF_ALL_CREDITS = True
        return

    def aws_crypto_info_check(self) -> None:
        ''' Compares length of CRYPTO_INFO dictionary to a file stored in amazon AWS s3 and updated online file
            if the local file is larger than online one by REUPLOAD_DIFFERENCE (currently 10).
            Used as background scheduled task.
            NOTE: Using free version of AWS S3 -> limited number of push/pull requests for free. So trying to save some here.
        '''
        self.SESSION_NUM_SAVED = len(self.CRYPTO_INFO)
        if self.SESSION_NUM_SAVED >= (self.NUMBER_OF_SAVED_CRYPTO_INFO + self.REUPLOAD_DIFFERENCE):
            try:
                with open(os.path.join(self.CRYPTO_INFO_PICKLE_PATH), "wb") as f:
                    pickle.dump(self.CRYPTO_INFO, f)
                    print('Updated existing crypto_info pickle (total {0} tokens).'.format(len(self.CRYPTO_INFO)))
            except Exception as e:
                print('PICKLE ERROR DURING AWS CHECK: {}'.format(e))
            try:
                self.AWS.upload_file(self.CRYPTO_INFO_PICKLE_NAME, self.CRYPTO_INFO_PICKLE_PATH)
                print('Uploaded {} to AWS successfully'.format(self.CRYPTO_INFO_PICKLE_NAME))
            except Exception as e:
                print(f'Failed to upload pickle to AWS or load file frm pickle.{e}')

    def round_nonzero(self, number: Union[int, float], digits_to_keep: int = 4) -> str:
        ''' A utility function to round numbers to first NON-ZERO digits. Returns a string.
            Example: 0.00005412323132 -> 0.000054
        '''
        assert isinstance(number, (float, int)), 'Argument "number" must be of type "float" or "int". You provided: "{}"'.format(type(number))
        if number == 0:
            return number
        number = float(number)
        if number < 0:
            negative_number = True
        else:
            negative_number = False
        n = abs(number)
        if n < 1:
            # Find the first non-zero digit.
            # We want 3 digits, starting at that location.
            s = f'{n:.99f}'
            index = re.search('[1-9]', s).start()
            result = s[:index + digits_to_keep]
            if negative_number is True:
                return str(-(float(result)))  # Converting number back to negative
            else:
                return result
        else:
            # We want 2 digits after decimal point.
            result = str(round(n, digits_to_keep))
            if negative_number is True:
                return str(-(float(result)))  # Converting number back to negative
            else:
                return result

    def load_symbols_from_pickle(self, pickle_file_name: str, expiration_hours: int = 24) -> list or None:
        ''' A utility function to load .pickle files. Has optional argument "expiration_hours". if file is older than this var -> return None.
            Explanation for "expiration_hours" logic: Don't want to load data that might be "outdated".
            So will do a new request somehwere else in the code if file is "expired".
        '''
        if '.pickle' not in pickle_file_name:  # if file name provided contains no extension -> add it.
            pickle_file_name += '.pickle'
        full_path = os.path.join(self.DIR_PATH, pickle_file_name)  # NOTE: Only looks in directory where this script is running

        if not os.path.exists(full_path):
            print(f'{pickle_file_name} doesnt exist')
            return None

        # The part that checks when was pickle file last updated. If above 'expiration_hours' -> ignore it and return None
        if expiration_hours is not None:
            threshold = datetime.timedelta(hours=expiration_hours)  # can also be minutes, seconds, etc.
            now = time.time()
            file_last_update_time = os.path.getmtime(full_path)
            delta = datetime.timedelta(seconds=now-file_last_update_time)
            if delta > threshold:
                print(f'{pickle_file_name} was last updated more than {expiration_hours} hours ago. Not loading it...')
                return None

        try:
            with open(full_path, "rb") as f:
                pickle_data = pickle.load(f)
        except Exception as e:
            print(e)
            return None
        print(f'Pickle "{pickle_file_name}" loaded successfully.')
        return pickle_data


# Example:
if __name__ == '__main__':
    try:
        CP = CMCPrices()
        crypto_info = CP.get_crypto_info(['TRX'], save_pickle=True)
        from pprint import pprint
        pprint(crypto_info)
    except KeyboardInterrupt:
        quit()
