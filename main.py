import argparse
import json
import logging
import os.path
import time
from datetime import datetime

import coloredlogs
import telegram
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import Select

URL = 'https://www.vfsvisaonline.com/Netherlands-Global-Online-Appointment_Zone2/AppScheduling/AppWelcome.aspx?P=OG3X2CQ4L1NjVC94HrXIC7tGMHIlhh8IdveJteoOegY%3D'
CITY = 'Moscow'
VISA_CATEGORY = 'MVV – visa for long stay (>90 days)'
NO_DATES_MARKER = 'No date(s) available for appointment'

USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 ' \
             '(KHTML, like Gecko) Chrome/100.0.4896.127 Safari/537.36'


logger = logging.getLogger(__name__)


def get_driver(headless=True, scale_factor=2.0):
    path = './bin/chromedriver_v100.exe'
    path = os.path.abspath(path)

    options = webdriver.ChromeOptions()
    options.add_argument(f'--user-agent={USER_AGENT}')
    options.add_argument('window-size=1024,768')
    options.add_argument(f'high-dpi-support={scale_factor}')
    options.add_argument(f'force-device-scale-factor={scale_factor}')
    options.add_argument('--log-level=3')  # disable logs

    if headless:
        options.add_argument('--headless')
        options.add_argument('--disable-gpu')

    return webdriver.Chrome(path, options=options)


def ensure_dir(path):
    dir_path = os.path.dirname(path)
    os.makedirs(dir_path, exist_ok=True)


def get_time_prefix():
    now = datetime.now()
    return now.strftime('%Y-%m-%d %H-%M-%S-%f')


def get_screenshot_path(name='default'):
    path = f'./artifacts/screenshots/{get_time_prefix()}-{name}.png'
    ensure_dir(path)
    return path


def save_page_source(page_source, stage):
    path = f'./artifacts/pages/{get_time_prefix()}-{stage}.html'
    ensure_dir(path)
    with open(path, 'w') as f:
        f.write(page_source)


class SlotsCheckResults:
    def __init__(self, found, screenshot):
        self.found = found
        self.screenshot = screenshot


def page_trace(driver, checkpoint, screenshot=True):
    save_page_source(driver.page_source, checkpoint)

    if screenshot:
        driver.save_screenshot(get_screenshot_path(checkpoint))


def check_available_slots(driver):
    driver.get(URL)

    page_trace(driver, 'loaded')

    schedule_link = driver.find_element(By.LINK_TEXT, 'Schedule Appointment')
    schedule_link.click()

    page_trace(driver, 'schedule-clicked')

    city_picker = driver.find_element(By.ID, 'plhMain_cboVAC')
    city_picker_select = Select(city_picker)
    city_picker_select.select_by_visible_text(CITY)

    city_submit_btn = driver.find_element(By.ID, 'plhMain_btnSubmit')
    city_submit_btn.click()

    page_trace(driver, 'city-submitted')

    category_picker = driver.find_element(By.ID, 'plhMain_cboVisaCategory')
    category_picker_select = Select(category_picker)
    category_picker_select.select_by_visible_text(VISA_CATEGORY)

    continue_btn = driver.find_element(By.ID, 'plhMain_btnSubmit')
    continue_btn.click()

    page_trace(driver, 'before-calendar')

    message_span = driver.find_element(By.ID, 'plhMain_lblMsg')

    slots_found = NO_DATES_MARKER not in message_span.text

    logger.info('SLOTS FOUND? %s', slots_found)

    page_screenshot = driver.get_screenshot_as_png()
    calendar_screenshot = None

    if slots_found:
        try:
            given_name_textbox = driver.find_element(By.ID, 'plhMain_repAppVisaDetails_tbxFName_0')
            surname_textbox = driver.find_element(By.ID, 'plhMain_repAppVisaDetails_tbxLName_0')
            contact_number_textbox = driver.find_element(By.ID, 'plhMain_repAppVisaDetails_tbxContactNumber_0')
            email_textbox = driver.find_element(By.ID, 'plhMain_repAppVisaDetails_tbxEmailAddress_0')

            given_name_textbox.send_keys('GIVENNAME')
            surname_textbox.send_keys('SURNAME')
            contact_number_textbox.send_keys('79170000000')
            email_textbox.send_keys('tracker@gmail.com')
            confirm_picker = driver.find_element(By.ID, 'plhMain_cboConfirmation')
            confirm_picker_select = Select(confirm_picker)
            confirm_picker_select.select_by_visible_text('I confirm the above statement')

            submit_btn = driver.find_element(By.ID, 'plhMain_btnSubmit')
            submit_btn.click()

            page_trace(driver, 'calendar')

            calendar_table = driver.find_element(By.ID, 'plhMain_cldAppointment')
            calendar_screenshot = calendar_table.screenshot_as_png
        except Exception:
            logger.error('Unable to get result screenshot', exc_info=True)

    return SlotsCheckResults(slots_found, calendar_screenshot or page_screenshot)


def read_config():
    with open('config.json', 'r') as f:
        return json.loads(f.read())


def require_config_key(config, config_key):
    if config_key not in config:
        raise RuntimeError('"%s" config key expected')
    return config[config_key]


def check_once():
    logger.debug('starting')
    driver = get_driver()
    try:
        config = read_config()
        logger.debug('config: %s', config)

        telegram_chat_id = require_config_key(config, 'telegram_chat_id')
        telegram_bot_token = require_config_key(config, 'telegram_bot_api_token')

        bot = telegram.Bot(telegram_bot_token)

        result = check_available_slots(driver)

        if result.found:
            bot.send_message(chat_id=telegram_chat_id, text='Slots found!')
            if result.screenshot:
                bot.send_photo(chat_id=telegram_chat_id, photo=result.screenshot)
                # bot.send_document(chat_id=telegram_chat_id, filename='calendar.png', document=result.screenshot)
            bot.send_message(chat_id=telegram_chat_id, text=URL)
        else:  # no slots found
            # bot.send_message(chat_id=telegram_chat_id, text='Did not find any slots...')
            # bot.send_photo(chat_id=telegram_chat_id, photo=driver.get_screenshot_as_png())
            # bot.send_message(chat_id=telegram_chat_id, text=URL)
            pass

        logger.debug('done')
    except Exception:
        driver.save_screenshot(get_screenshot_path('error'))
        logger.exception('An error occurred')
    finally:
        logger.debug('closing driver')
        driver.close()


def monitor(period_seconds):
    while True:
        check_once()
        time.sleep(period_seconds)


if __name__ == '__main__':
    logging.basicConfig(
        filename='app.log',
        format='%(asctime)s %(levelname)s:%(message)s',
        level=logging.DEBUG)
    coloredlogs.install(level=logging.DEBUG)

    parser = argparse.ArgumentParser()
    parser.add_argument('--log-level', type=str, default='INFO', required=False)

    subparsers = parser.add_subparsers()

    check_parser = subparsers.add_parser('check')
    check_parser.set_defaults(command='check')

    monitor_parser = subparsers.add_parser('monitor')
    monitor_parser.add_argument('--period-seconds', type=int, default=300, required=False)
    monitor_parser.set_defaults(command='monitor')

    args = parser.parse_args()

    log_level = args.log_level.upper()

    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)
    coloredlogs.set_level(log_level)

    logger.info('parsed args: %s', args)

    if args.command == 'check':
        check_once()
    else:
        monitor(period_seconds=args.period_seconds)
