import argparse
import collections
import itertools
import json
import logging
import os.path
import time
from datetime import datetime
from typing import List, OrderedDict

import coloredlogs
import pytz
import telegram
import telegram.ext
from selenium import webdriver
from selenium.common.exceptions import NoSuchElementException
from selenium.webdriver.common.by import By
from selenium.webdriver.firefox.service import Service as FFService
from selenium.webdriver.support.ui import Select

URL = 'https://www.vfsvisaonline.com/Netherlands-Global-Online-Appointment_Zone2/AppScheduling/AppWelcome.aspx?P=OG3X2CQ4L1NjVC94HrXIC7tGMHIlhh8IdveJteoOegY%3D'
CITY = 'Moscow'
VISA_CATEGORY = 'MVV – visa for long stay (>90 days)'
NO_DATES_MARKER = 'No date(s) available for appointment'

USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 ' \
             '(KHTML, like Gecko) Chrome/100.0.4896.127 Safari/537.36'


logger = logging.getLogger(__name__)


def get_chrome_driver(path, headless=True, scale_factor=2.0):
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


def get_firefox_driver(path, headless=True, scale_factor=2.0):
    path = os.path.abspath(path)

    options = webdriver.FirefoxOptions()
    options.headless = headless
    options.set_preference('layout.css.devPixelsPerPx''', str(scale_factor))

    service = FFService(path)

    driver = webdriver.Firefox(service=service, options=options)

    driver.set_window_position(0, 0)
    driver.set_window_size(1280, 1080)

    driver.set_page_load_timeout(30)
    driver.implicitly_wait(10)

    return driver


def get_driver_loader(driver_type):
    if driver_type == 'firefox':
        return get_firefox_driver
    elif driver_type == 'chrome':
        return get_chrome_driver
    else:
        raise RuntimeError('Unknown driver type: %s' % driver_type)


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


# time is represented by string HHMM (4 characters)
class AvailableSlot:
    def __init__(self, month: str, day: int, time: str):
        self.month = month
        self.day = day
        self.time = time

    def __eq__(self, other):
        return (self.month == other.month and
                self.day == other.day and
                self.time == other.time)

    def __repr__(self):
        return f'<{self.month} on {self.day} at {self.time}>'

    def to_dict(self):
        return {
            'month': self.month,
            'day': self.day,
            'time': self.time,
        }

    @staticmethod
    def from_dict(data):
        return AvailableSlot(data['month'], data['day'], data['time'])


class SlotsCheckResults:
    def __init__(self, slots: List[AvailableSlot], screenshots: List[bytes]):
        self.slots = slots
        self.screenshots = screenshots


def page_trace(driver, checkpoint, screenshot=True):
    save_page_source(driver.page_source, checkpoint)

    if screenshot:
        driver.save_screenshot(get_screenshot_path(checkpoint))


def find_element_safe(driver, by, value):
    try:
        return driver.find_element(by, value)
    except NoSuchElementException:  # spelling error making this code not work as expected
        return None


def parse_available_times_in_day(driver) -> List[str]:
    slots_table = driver.find_element(By.ID, 'plhMain_gvSlot')
    times = []
    for row in slots_table.find_elements(By.TAG_NAME, 'tr')[1:]:
        times.append(row.text)
    return times


def parse_available_dates(driver) -> List[AvailableSlot]:
    calendar_element = driver.find_element(By.ID, 'plhMain_cldAppointment')
    month: str = calendar_element.find_elements(By.TAG_NAME, 'tr')[0].text
    month = month.replace('>>', '').replace('<<', '').strip()

    # day -> slots
    available_slots = {}

    # when we navigate to another page the reference to the
    # found element becomes invalid
    while True:
        # update element
        calendar_element = driver.find_element(By.ID, 'plhMain_cldAppointment')
        day_elements = calendar_element.find_elements(By.CLASS_NAME, 'OpenDateAllocated')

        # try to find not yet parsed day
        day_element = next((el for el in day_elements if int(el.text) not in available_slots), None)

        if day_element is None:
            break  # parsed all

        day = int(day_element.text)

        day_link = day_element.find_element(By.TAG_NAME, 'a')
        day_link.click()

        times = parse_available_times_in_day(driver)

        back_link = driver.find_element(By.ID, 'plhMain_btnBack')
        back_link.click()

        available_slots[day] = [AvailableSlot(month, day, time) for time in times]

    return list(itertools.chain(*available_slots.values()))


def get_available_slots_diff(baseline: collections.OrderedDict, current: collections.OrderedDict):
    diff = collections.OrderedDict()

    for month in baseline:
        removed_dates = set(baseline[month]) - set(current.get(month, []))
        if removed_dates:
            diff.setdefault(month, {})['removed'] = sorted(removed_dates)

    for month in current:
        added_dates = set(current[month]) - set(baseline.get(month, []))
        if added_dates:
            diff.setdefault(month, {})['added'] = sorted(added_dates)

    return diff


def is_no_dates_available_marker_present(driver):
    message_span = find_element_safe(driver, By.ID, 'plhMain_lblMsg')
    return message_span and NO_DATES_MARKER in message_span.text


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

    if is_no_dates_available_marker_present(driver):
        logger.info('No slots found')
        page_screenshot = driver.get_screenshot_as_png()
        return SlotsCheckResults([], screenshots=[page_screenshot])

    logger.debug('Looks like there are some slots, getting the calendar')

    # slots seem to be found, get the calendar
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

    # no dates marker can be present on later stage too
    if is_no_dates_available_marker_present(driver):
        logger.info('No slots found')
        page_screenshot = driver.get_screenshot_as_png()
        return SlotsCheckResults([], screenshots=[page_screenshot])

    calendar_screenshots = []
    available_slots = []

    while True:
        calendar_table = driver.find_element(By.ID, 'plhMain_cldAppointment')

        calendar_screenshot = calendar_table.screenshot_as_png
        calendar_screenshots.append(calendar_screenshot)

        month_slots = parse_available_dates(driver)
        available_slots.extend(month_slots)

        next_month_link = driver.find_element(By.LINK_TEXT, '>>')
        next_month_link.click()

        end_of_slots_marker = 'No date(s) available for current month'
        no_slots_element = find_element_safe(
            driver, By.XPATH, '//*[contains(text(), "%s")]' % end_of_slots_marker)

        if no_slots_element:
            break

        page_trace(driver, 'calendar')

    logger.debug('available dates: %s', available_slots)

    return SlotsCheckResults(available_slots, calendar_screenshots)


def read_config():
    path = 'config.json'
    # to simplify development
    if os.path.exists('local.config.json'):
        path = 'local.config.json'
    with open(path, 'r') as f:
        return json.loads(f.read())


def read_state():
    path = 'state.json'
    if not os.path.exists(path):
        return {}
    with open('state.json', 'r') as f:
        return json.loads(f.read())


def save_state(state):
    with open('state.json', 'w') as f:
        f.write(json.dumps(state))


def require_config_key(config, config_key):
    if config_key not in config:
        raise RuntimeError('"%s" config key expected')
    return config[config_key]


def check_once():
    logger.debug('starting')

    driver = None

    try:
        config = read_config()
        logger.debug('config: %s', config)

        driver_path = require_config_key(config, 'driver_path')

        driver_loader_fn = get_driver_loader(config.get('driver_type', 'firefox').lower())
        driver = driver_loader_fn(driver_path)

        telegram_chat_id = require_config_key(config, 'telegram_chat_id')
        telegram_bot_token = require_config_key(config, 'telegram_bot_api_token')

        bot = telegram.ext.ExtBot(telegram_bot_token, defaults=telegram.ext.Defaults(
            timeout=10,
        ))

        state = read_state()
        result = check_available_slots(driver)

        def get_available_dates(slots: List[AvailableSlot]) -> OrderedDict[str, List[int]]:
            result = collections.defaultdict(set)  # month -> days
            for slot in slots:
                result[slot.month].add(slot.day)
            return collections.OrderedDict(
                sorted([(k, sorted(v)) for k, v in result.items()], key=lambda x: x[0]))

        available_dates = get_available_dates(result.slots)
        prev_available_slots = [
            AvailableSlot.from_dict(x)
            for x in state.get('available_slots', [])
        ]
        prev_available_dates = get_available_dates(prev_available_slots)

        if prev_available_dates != available_dates:
            logger.info('notifying about state change')

            if available_dates:
                if not prev_available_dates:
                    notification_text = '🔥 Found available days!'
                else:
                    notification_text = '⚡ Available days changed!'

                media = []
                for screenshot in result.screenshots:
                    media.append(telegram.InputMediaPhoto(screenshot))

                # add the diff
                diff = get_available_slots_diff(prev_available_dates, available_dates)
                diff_description = ''
                for month in diff:
                    for day in diff[month].get('removed', []):
                        diff_description += '❌ %s %s\n' % (day, month)
                    for day in diff[month].get('added', []):
                        available_times = [
                            slot.time[:2] + ':' + slot.time[2:]
                            for slot in result.slots if slot.month == month and slot.day == day
                        ]
                        assert len(available_times) > 0
                        diff_description += '🟢 %s %s (%s)\n' % (day, month, ', '.join(available_times))

                notification_text += '\n\n' + diff_description
                notification_text += '\n' + URL

                # attach text to the first screenshot to be displayed
                media[0].caption = notification_text

                bot.send_media_group(chat_id=telegram_chat_id, media=media)
            else:  # no slots found
                bot.send_message(chat_id=telegram_chat_id, text='🙅 No more slots available...')
        else:
            logger.debug('State did not change, do not notify')

        status_message_id = config.get('telegram_status_message_id')
        if status_message_id:
            tz = pytz.timezone('Europe/Moscow')
            now = datetime.now(tz)
            now_string = now.strftime('%H:%M on %b %d')
            status = '⚡ Last checked at %s (Moscow time)' % now_string
            bot.edit_message_text(chat_id=telegram_chat_id, message_id=status_message_id, text=status)

        save_state(dict(
            state,
            available_slots=[slot.to_dict() for slot in result.slots],
            timestamp=time.time()
        ))

        logger.debug('done')
    except Exception:
        if driver:
            driver.save_screenshot(get_screenshot_path('error'))
        logger.exception('An error occurred')
    finally:
        logger.debug('closing driver')
        if driver:
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
    monitor_parser.add_argument('--period-seconds', type=int, default=15*60, required=False)
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
