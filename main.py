import os
import logging
from uuid import uuid4

from telegram import LabeledPrice, ReplyKeyboardMarkup, KeyboardButton, InlineQueryResultArticle, InputTextMessageContent
from telegram.ext import PreCheckoutQueryHandler, InlineQueryHandler
from telegram.ext.callbackcontext import CallbackContext
from telegram.ext.commandhandler import CommandHandler
from telegram.ext.filters import Filters
from telegram.ext.messagehandler import MessageHandler
from telegram.ext.updater import Updater
from telegram.update import Update
import emoji

from coinmarketcap import CMCPrices
from config_class import API_PROFILES


# Bool that controls whether the App will run through Heroku or locally. If False -> runs locally.
RUN_THROUGH_HEROKU = True

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logging.getLogger('apscheduler.executors.default').setLevel(logging.WARNING)  # removes log print everytime a scheduled task was run.
logger = logging.getLogger(__name__)

PORT = int(os.environ.get('PORT', '8443'))

TELEGRAM_TOKEN = API_PROFILES['TELEGRAM_TOKEN']
STRIPE_TOKEN = API_PROFILES['STRIPE_TOKEN']
HEROKU_URL_NAME = API_PROFILES['HEROKU_URL_NAME']

DONATE_PRICE = 200  # 2$. Price that appears on /donate command.
DONATE_MAX_TIP = 150000  # 1500$. Internal, users don't see this.
DONATE_SUGGESTED_TIPS = [69, 420, 6666, 133700]  # Memez... 0.69$, 4.20$, 66.66$, 1337$. Suggested tips that user sees when clicks "Pay Invoice" in /donate

DIR_PATH = os.path.dirname(os.path.abspath(__file__))

# Path to GIFs that appear when user interacts with the bot.
DONATE_FULL_PATH = os.path.join(DIR_PATH, 'extras/donate.gif')
AFTER_DONATE_FULL_PATH = os.path.join(DIR_PATH, 'extras/after_donate.gif')
OOM_FULL_PATH = os.path.join(DIR_PATH, 'extras/oom.jpg')

EMOJIS = {
    "hello": emoji.emojize(':waving_hand:'),  # Raised Hand with Fingers Splayed
    "shrug": emoji.emojize(':person_shrugging:'),  # Woman Shrugging
    "rocket": emoji.emojize(':rocket:'),  # Rocket
    "heart": emoji.emojize(':red_heart:'),  # Red Heart
}

CP = CMCPrices()


def start(update, context: CallbackContext) -> None:
    """Send a message when the command /start is issued."""
    first_name = update.message.chat.first_name
    last_name = update.message.chat.last_name
    keyboard = [[KeyboardButton("/example")], ['/crypto'], ['/fiat'], ['/donate']]
    reply_markup = ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True)
    update.message.reply_text('{0} Hey {1} {2}!\n\n'
                              ' Write a crypto token like "BTC" to get its price in USD and other useful information.'
                              ' You can specify other fiat currency than USD by adding its symbol after crypto.\n\n'
                              '{3} Example: "BTC" or "ETH EUR"'.format(EMOJIS['hello'], first_name, last_name, EMOJIS['shrug']),
                              reply_markup=reply_markup)


def example(update, context: CallbackContext) -> None:
    """Send a message when the command /example is issued.
       Creates a keyboard with two buttons for users to press.
    """
    keyboard = [['BTC', 'ETH EUR']]
    reply_markup = ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True)
    text_msg = 'Choose a crypto symbol and (optionally) currency in the menu below!'
    update.message.reply_text(text_msg, reply_markup=reply_markup)


def coinmarketcapHandler(update: Update, context: CallbackContext) -> None:
    """Send a crypto token quote (price,other info) via CoinmarketCap API when user types a valid crypto symbol (and fiat currency)
       Ignores casing. Supports only 1 crypto per request. Crypto and fiat must be separated by a whitespace.
       Examples: BTC, ETH EUR, bnb, sol dkk
    """
    user_message = str(update.message.text)
    split_input = user_message.split(' ', maxsplit=1)
    if len(split_input) == 1:
        crypto_symbol = split_input[0]
        token_info, status = CP.getCryptoPrice(crypto_symbol)
    elif len(split_input) == 2:
        crypto_symbol, currency = split_input[0], split_input[1]
        token_info, status = CP.getCryptoPrice(crypto_symbol, currency)
    if status is False:
        update.message.reply_text(text=token_info, parse_mode='Markdown')
        oom_img = open(OOM_FULL_PATH, 'rb').read()
        context.bot.sendPhoto(chat_id=update.message.chat_id, photo=oom_img)
    else:
        if token_info is not None:
            update.message.reply_text(text=token_info, parse_mode='Markdown', disable_web_page_preview=True)
        else:
            update.message.reply_text(text=status)


def print_all_cmc_cryptos(update: Update, context: CallbackContext) -> None:
    """Send a message when the command /crypto is issued.
       Prints all SYMBOLS of crypto tokens listed on Coinmarketcap (for some reason only 10k)
    """
    crypto_list, status = CP.PrintSupportedCryptos()
    if status is False:
        update.message.reply_text(text=crypto_list, parse_mode='Markdown')
        oom_img = open(OOM_FULL_PATH, 'rb').read()
        context.bot.sendPhoto(chat_id=update.message.chat_id, photo=oom_img)
    else:
        for idx, one_msg in enumerate(crypto_list):
            update.message.reply_text(one_msg)


def print_all_cmc_fiats(update: Update, context: CallbackContext) -> None:
    """Send a message when the command /fiat is issued.
       Prints all SYMBOLS of fiat currencies listed on Coinmarketcap
    """
    fiat_list, status = CP.PrintSupportedFiats()
    if status is False:
        update.message.reply_text(text=fiat_list, parse_mode='Markdown')
        oom_img = open(OOM_FULL_PATH, 'rb').read()
        context.bot.sendPhoto(chat_id=update.message.chat_id, photo=oom_img)
    else:
        update.message.reply_text(fiat_list)


def print_cmc_usage_info(update: Update, context: CallbackContext) -> None:
    """Send a message when the command /secret is issued.
       Prints API key details and usage stats.
    """
    key_info = CP.PrintKeyInfo()
    update.message.reply_text(key_info)


def error(update: Update, context: CallbackContext) -> None:
    """Log Errors caused by Updates."""
    logger.warning('Update "%s" caused error "%s"', update, context.error)


def pre_checkout_handler(update: Update, context: CallbackContext) -> None:
    """https://core.telegram.org/bots/api#answerprecheckoutquery"""
    query = update.pre_checkout_query
    query.answer(ok=True)


def donate(update: Update, context: CallbackContext) -> None:
    '''Creates a pay button invoice when users uses /donate command.
       Currently uses STRIPE as a payment provider. For testing purposes, use TEST STRIPE token defined at the top of this file'''
    first_name = update.message.chat.first_name
    last_name = update.message.chat.last_name
    context.bot.send_invoice(
        chat_id=update.message.chat_id,
        title="Cheers {0} {1} {2}".format(first_name, last_name, EMOJIS['heart']),
        description="If you like this bot feel free to buy me a coffee.",
        payload="donation",
        provider_token=STRIPE_TOKEN,
        currency="USD",
        prices=[LabeledPrice("Give", DONATE_PRICE)],
        need_name=False,
        max_tip_amount=DONATE_MAX_TIP,
        suggested_tip_amounts=DONATE_SUGGESTED_TIPS,
        start_parameter=None,
    )
    donate_gif = open(DONATE_FULL_PATH, 'rb').read()
    context.bot.sendAnimation(chat_id=update.message.chat_id, animation=donate_gif)


def successful_payment_callback(update: Update, context: CallbackContext) -> None:
    '''A reponse that user sees when /donate'ion was successful.
       Currently sends a text message and GIF.'''
    update.message.reply_text('Thank you for the donation! {0} {0} {0}'.format(EMOJIS['rocket']))
    after_donate_gif = open(AFTER_DONATE_FULL_PATH, 'rb').read()
    context.bot.sendAnimation(chat_id=update.message.chat_id, animation=after_donate_gif)


def inline_query(update: Update, context: CallbackContext) -> None:
    """Handling the inline queries (bot requests from other chats).
       This is run when you type: @botusername <query>.
    """
    query = update.inline_query.query

    if query == "":
        return

    if query == 'start' or query == 'help':
        str_1 = 'Write a crypto token like "BTC" to get its price in USD and other useful information.\n'
        str_2 = 'You can specify other fiat currency than USD by adding its symbol after crypto.\n'
        str_3 = '{0} Example: "BTC" or "ETH EUR"'.format(EMOJIS['shrug'])
        reply_text = f'{str_1}{str_2}{str_3}'
        results = [InlineQueryResultArticle(id=str(uuid4()),
                   title="Help Reference",
                   description='Sends help message on how to use the bot',
                   input_message_content=InputTextMessageContent(reply_text))]

        update.inline_query.answer(results)
    else:
        split_input = query.split(' ', maxsplit=1)
        if len(split_input) == 1:
            crypto_symbol = split_input[0]
            token_info, status = CP.getCryptoPrice(crypto_symbol)
        elif len(split_input) == 2:
            crypto_symbol, currency = split_input[0], split_input[1]
            token_info, status = CP.getCryptoPrice(crypto_symbol, currency)
        if status is False:
            update.message.reply_text(text=token_info, parse_mode='Markdown')
            oom_img = open(OOM_FULL_PATH, 'rb').read()
            context.bot.sendPhoto(chat_id=update.message.chat_id, photo=oom_img)
        else:
            if token_info is not None:
                reply_text = token_info
            else:
                reply_text = '{} - Token not found on CoinMarketCap.'.format(crypto_symbol)

        results = [InlineQueryResultArticle(id=str(uuid4()),
                   title="Get crypto price",
                   description='Shows latest crypto price in chat.',
                   input_message_content=InputTextMessageContent(reply_text, parse_mode='Markdown', disable_web_page_preview=True))]

        update.inline_query.answer(results)


def main() -> None:
    """Start the Telegram bot."""
    updater = Updater(TELEGRAM_TOKEN, use_context=True)

    # Get the dispatcher to register handlers
    dp = updater.dispatcher

    # Command handlers.
    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("donate", donate, run_async=True))
    dp.add_handler(CommandHandler("example", example))
    dp.add_handler(CommandHandler("crypto", print_all_cmc_cryptos, run_async=True))
    dp.add_handler(CommandHandler("fiat", print_all_cmc_fiats, run_async=True))
    dp.add_handler(CommandHandler("secret", print_cmc_usage_info, run_async=True))

    # Inline query handler
    dp.add_handler(InlineQueryHandler(inline_query, run_async=True))

    # General chat message handler.
    dp.add_handler(MessageHandler(Filters.text & ~Filters.command, coinmarketcapHandler, run_async=True))

    # Payment processing handlers.
    dp.add_handler(PreCheckoutQueryHandler(pre_checkout_handler))
    dp.add_handler(MessageHandler(Filters._SuccessfulPayment(), successful_payment_callback))

    # Error logging
    dp.add_error_handler(error)

    if RUN_THROUGH_HEROKU is True:
        # Cloud running
        updater.start_webhook(listen="0.0.0.0",
                              port=int(PORT),
                              url_path=TELEGRAM_TOKEN,
                              webhook_url='https://{0}.herokuapp.com/{1}'.format(HEROKU_URL_NAME, TELEGRAM_TOKEN))
    else:
        # Local running
        updater.start_polling()  # NOTE: Run this for local code running.

    updater.idle()


if __name__ == '__main__':
    main()
