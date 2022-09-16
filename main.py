import argparse
import collections
import itertools
import json
import logging
import os.path
import pickle
import re
import time
from datetime import datetime
from io import BytesIO
from typing import List, OrderedDict, Dict, Any

import coloredlogs
import pytz
import telegram
import telegram.ext
import undetected_chromedriver
from PIL import Image
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.desired_capabilities import DesiredCapabilities
from selenium.webdriver.common.proxy import Proxy, ProxyType
from selenium.webdriver.firefox.service import Service as FFService
from selenium.webdriver.remote.webdriver import WebDriver, WebElement
from selenium.webdriver.support.ui import Select

import captcha.solver
import utils
from model import AvailableSlot, SlotsCheckResults
from proxy_host import ProxyHost

URL = 'https://www.vfsvisaonline.com/Netherlands-Global-Online-Appointment_Zone2/AppScheduling/AppWelcome.aspx?P=OG3X2CQ4L1NjVC94HrXIC7tGMHIlhh8IdveJteoOegY%3D'
CITY = 'Moscow'
VISA_CATEGORY = 'MVV – visa for long stay (>90 days)'
NO_DATES_MARKER = re.compile(
    r'(No date\(s\) available for appointment)|(No Appointment slots available)')

USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 ' \
             '(KHTML, like Gecko) Chrome/100.0.4896.127 Safari/537.36'


logger = logging.getLogger(__name__)


def get_chrome_driver(
        path: str,
        headless: bool = True,
        scale_factor: float = 2.0,
        proxy: Proxy = None) -> webdriver.Chrome:
    path = os.path.abspath(path)

    options = webdriver.ChromeOptions()
    options.add_argument(f'--user-agent={USER_AGENT}')
    options.add_argument('--log-level=3')  # disable logs
    options.add_argument('--start-maximized')
    # options.add_argument('--window-size=1024,768')

    # for some reason setting the DPI "the right way" does not work to get
    # elements screenshots in a good quality... it does work when capturing the
    # whole page, but not individual elements' screenshots
    # I've also tried working around by using page zoom level, but then the
    # element screenshot are not working correctly at all -- screenshots have
    # wrong parts of the page captured
    options.add_argument(f'--high-dpi-support={scale_factor}')
    options.add_argument(f'--force-device-scale-factor={scale_factor}')

    options.add_argument('--disable-blink-features')
    options.add_argument('--disable-blink-features=AutomationControlled')
    # options.add_experimental_option("excludeSwitches", ["enable-automation"])
    # options.add_experimental_option('useAutomationExtension', False)

    if headless:
        options.add_argument('--headless')
        options.add_argument('--disable-gpu')

    # this does not work for Chrome driver:
    # options.proxy = proxy

    if proxy:
        options.accept_insecure_certs = True
        options.add_argument('--proxy-server=http://%s' % proxy.httpProxy)

    # driver = webdriver.Chrome(path, options=options)
    driver = undetected_chromedriver.Chrome(
        driver_executable_path=path,
        options=options)

    driver.set_page_load_timeout(30)
    driver.implicitly_wait(10)

    return driver


def get_firefox_driver(
        path: str,
        headless: bool = True,
        scale_factor: float = 2.0,
        proxy: Proxy = None) -> webdriver.Firefox:
    path = os.path.abspath(path)

    options = webdriver.FirefoxOptions()
    options.headless = headless
    options.set_preference('layout.css.devPixelsPerPx''', str(scale_factor))
    options.accept_insecure_certs = True
    options.proxy = proxy

    # avoid self-identification
    options.set_preference("dom.webdriver.enabled", False)
    options.set_preference('useAutomationExtension', False)

    service = FFService(path)

    driver = webdriver.Firefox(
        service=service,
        options=options,
        desired_capabilities=DesiredCapabilities.FIREFOX
    )

    driver.set_window_position(0, 0)
    driver.set_window_size(1280, 1080)

    driver.set_page_load_timeout(30)
    driver.implicitly_wait(10)

    return driver


def get_driver_loader(driver_type: str):
    if driver_type == 'firefox':
        return get_firefox_driver
    elif driver_type == 'chrome':
        return get_chrome_driver
    else:
        raise ValueError('Unknown driver type: %s' % driver_type)


def get_time_prefix() -> str:
    now = datetime.now()
    return now.strftime('%Y-%m-%d %H-%M-%S-%f')


def get_screenshot_path(name='default') -> str:
    path = f'./artifacts/screenshots/{get_time_prefix()}-{name}.png'
    utils.ensure_dir(path)
    return path


def save_page_source(page_source, stage) -> None:
    path = f'./artifacts/pages/{get_time_prefix()}-{stage}.html'
    utils.ensure_dir(path)
    with open(path, 'w') as f:
        f.write(page_source)


def page_trace(driver: WebDriver, checkpoint: str, screenshot:bool = True) -> None:
    save_page_source(driver.page_source, checkpoint)

    if screenshot:
        path = get_screenshot_path(checkpoint)
        driver.save_screenshot(path)


def parse_available_times_in_day(driver: WebDriver) -> List[str]:
    slots_table = driver.find_element(By.ID, 'plhMain_gvSlot')
    times = []
    for row in slots_table.find_elements(By.TAG_NAME, 'tr')[1:]:
        times.append(row.text)
    return times


def parse_available_dates(driver: WebDriver) -> List[AvailableSlot]:
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


def is_no_dates_available_marker_present(driver: WebDriver):
    message_span = utils.find_element_safe(driver, By.ID, 'plhMain_lblMsg')
    return message_span and NO_DATES_MARKER.search(message_span.text)


def is_captcha_screen_present(driver: WebDriver):
    captcha_marker = utils.find_element_safe(
        driver, By.XPATH, '//h2[contains(text(), "%s")]' %
                          'Checking if the site connection is secure')
    return captcha_marker is not None


def element_screenshot(driver: WebDriver, element: WebElement):
    if isinstance(driver, webdriver.Chrome):
        return element_screenshot_chrome(driver, element)
    return element.screenshot_as_png


def element_screenshot_chrome(driver: webdriver.Chrome, element: WebElement):
    driver.execute_script("arguments[0].scrollIntoView(true);", element)
    screenshot_png = driver.get_screenshot_as_png()
    screenshot_img = Image.open(BytesIO(screenshot_png))
    location, size = element.location_once_scrolled_into_view, element.size
    win_size = driver.get_window_size()
    win_h, win_w = win_size['height'], win_size['width']
    x, y = location['x'], location['y']
    h, w = size['height'], size['width']

    h, w = min(win_h, h), min(win_w, w)

    # TODO: get rid of hard-coded scale -- retrieve from the driver settings
    scale = 2

    x = x * scale
    y = y * scale
    w = w * scale
    h = h * scale

    cropped_img = screenshot_img.crop(
        (x, y, x + w, y + h)
    )

    img_bytes = BytesIO()
    cropped_img.save(img_bytes, format='PNG')
    return img_bytes.getvalue()


def save_image(data: bytes, path: str):
    with open(path, 'wb') as f:
        f.write(data)


def check_available_slots(driver: WebDriver):
    driver.get(URL)

    page_trace(driver, 'loaded')

    if is_captcha_screen_present(driver):
        config = read_config()
        anticaptcha_api_key = config.get('anticaptcha_api_key')
        if anticaptcha_api_key:
            captcha.solver.solve_captcha(driver, anticaptcha_api_key)
        else:
            # pause to solve manually
            logger.warning('Detected captcha screen, adding 1 minute wait time. '
                           'Manually solve the captcha (disable headless mode '
                           'if required), then the cookies will be saved for the next '
                           'session')
            captcha_time = 60
            captcha_report_period = 10
            for captcha_report_round in range(captcha_time // captcha_report_period - 1):
                leftover = captcha_time - captcha_report_round * captcha_report_period
                logger.warning('%s seconds left...', leftover)
                time.sleep(10)

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

        calendar_screenshot = element_screenshot(driver, calendar_table)
        calendar_screenshots.append(calendar_screenshot)

        month_slots = parse_available_dates(driver)
        available_slots.extend(month_slots)

        next_month_link = driver.find_element(By.LINK_TEXT, '>>')
        next_month_link.click()

        end_of_slots_marker = 'No date(s) available for current month'
        no_slots_element = utils.find_element_safe(
            driver, By.XPATH, '//*[contains(text(), "%s")]' % end_of_slots_marker)

        if no_slots_element:
            break

        page_trace(driver, 'calendar')

    logger.debug('available dates: %s', available_slots)

    return SlotsCheckResults(available_slots, calendar_screenshots)


def read_config() -> Dict[str, Any]:
    logger.debug('reading configuration')
    probe_order = [
        'dev.config.json',
        'local.config.json',
        'config.json'
    ]

    for path in probe_order:
        if not os.path.exists(path):
            continue
        logger.debug('config path: %s', path)
        with open(path, 'r') as f:
            data = json.loads(f.read())
            logger.debug('config: %s', data)
            return data


def require_config_key(config: Dict[str, Any], config_key: str) -> Any:
    if config_key not in config:
        raise RuntimeError('"%s" config key expected' % config_key)
    return config[config_key]


def read_state() -> Dict[str, Any]:
    path = 'state.json'
    if not os.path.exists(path):
        return {}
    with open('state.json', 'r') as f:
        return json.loads(f.read())


def save_state(state: Dict[str, Any]):
    with open('state.json', 'w') as f:
        f.write(json.dumps(state))


def save_cookies(driver: WebDriver) -> None:
    cookies = driver.get_cookies()
    with open('cookies.dat', 'wb+') as f:
        pickle.dump(cookies, f)


def load_cookies(driver: WebDriver) -> None:
    if not os.path.exists('cookies.dat'):
        logger.info('cookies file not found')
        return

    with open('cookies.dat', 'rb') as f:
        cookies = pickle.load(f)
        for cookie in cookies:
            driver.add_cookie(cookie)


def check_once(headless: bool = None) -> None:
    logger.debug('starting')

    driver = None
    proxy_host = ProxyHost()

    try:
        config = read_config()

        driver_path = require_config_key(config, 'driver_path')
        driver_type = config.get('driver_type', 'firefox').lower()
        driver_loader_fn = get_driver_loader(driver_type)

        proxy_port = 8080
        proxy_host.start(port=proxy_port)

        proxy_config = Proxy()
        proxy_config.proxyType = ProxyType.MANUAL
        proxy_config.httpProxy = 'localhost:%d' % proxy_port
        proxy_config.sslProxy = 'localhost:%d' % proxy_port

        params = {}

        if headless is not None:
            params['headless'] = headless

        params['proxy'] = proxy_config

        driver = driver_loader_fn(driver_path, **params)

        logger.info('loading cookies...')
        # setting cookie requires current context to be matching domain
        driver.get(URL)
        load_cookies(driver)
        logger.info('loaded cookies')

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
                added_something = False
                for month in diff:
                    for day in diff[month].get('removed', []):
                        diff_description += '❌ %s %s\n' % (day, month)
                    for day in diff[month].get('added', []):
                        added_something = True
                        available_times = [
                            slot.formatted_time
                            for slot in result.slots
                            if slot.month == month and slot.day == day
                        ]
                        available_slot_count = len(available_times)
                        assert available_slot_count > 0
                        diff_description += '🟢 %s %s (%s %s)\n' % (
                            day, month, available_slot_count,
                            'slot' if available_slot_count == 1 else 'slots')

                notification_text += '\n\n' + diff_description
                notification_text += '\n' + URL

                # cut the message if too long to at least send it successfully
                if len(notification_text) > 1000:
                    notification_text = notification_text[:1000] + ' (cut)'

                # attach text to the first screenshot to be displayed
                media[0].caption = notification_text

                bot.send_media_group(
                    chat_id=telegram_chat_id,
                    media=media,
                    # do not notify unless we detected new slots
                    disable_notification=not added_something,
                )
            else:  # no slots found
                bot.send_message(
                    chat_id=telegram_chat_id,
                    text='🙅 No more slots available...',
                )
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

        logger.info('check completed')

        logger.info('saving cookies')
        save_cookies(driver)
        logger.info('cookies saved')
    except Exception:
        if driver:
            driver.save_screenshot(get_screenshot_path('error'))

            if not is_captcha_screen_present(driver):
                logger.info('saving cookies even with error occurred, because '
                            'captcha screen seems to be not present')
                save_cookies(driver)
        logger.exception('An error occurred')
        raise  # reraise exception
    finally:
        logger.debug('closing driver...')
        if driver:
            driver.close()
        logger.debug('stopping proxy...')
        proxy_host.stop()


def monitor(period_seconds: int, headless: bool = None) -> None:
    while True:
        try:
            check_once(headless=headless)
        except Exception:
            # swallow exceptions, they are logged anyway already
            pass
        time.sleep(period_seconds)


def bot_test(headless: bool = None) -> None:
    config = read_config()

    driver_path = require_config_key(config, 'driver_path')
    driver_type = config.get('driver_type', 'firefox').lower()
    driver_loader_fn = get_driver_loader(driver_type)

    params = {}

    if headless is not None:
        params['headless'] = headless

    driver = driver_loader_fn(driver_path, **params)

    driver.get('https://bot.sannysoft.com/')
    page_trace(driver, 'bot-test')

    for table_idx, table in enumerate(driver.find_elements(By.TAG_NAME, 'table')):
        logger.info('rect: %s, loc: %s, loc2: %s', table.rect, table.location, table.location_once_scrolled_into_view)
        driver.execute_script("arguments[0].scrollIntoView(true);", table)
        element_screenshot_path = get_screenshot_path(
            'bot-test-table-%s' % table_idx)
        screenshot_data = element_screenshot(driver, table)
        save_image(screenshot_data, element_screenshot_path)

    if headless is False:
        logger.info('waiting 10 seconds before exit...')
        time.sleep(10)


def str_to_bool(s):
    true_notions = ['yes', 'true', '1']
    false_notions = ['no', 'false', '0']

    s = s.lower()

    if s not in true_notions + false_notions:
        raise argparse.ArgumentTypeError('Expected boolean')

    return s in true_notions


if __name__ == '__main__':
    logging.basicConfig(
        filename='app.log',
        format='%(asctime)s %(levelname)s:%(message)s',
        level=logging.DEBUG)
    coloredlogs.install(level=logging.DEBUG)

    parser = argparse.ArgumentParser()
    parser.add_argument('--log-level', type=str, default=None, required=False)
    parser.add_argument('--headless', type=str_to_bool, default=True,
                        choices=[False, True])

    subparsers = parser.add_subparsers()

    check_parser = subparsers.add_parser('check')
    check_parser.set_defaults(command='check')

    monitor_parser = subparsers.add_parser('monitor')
    monitor_parser.add_argument('--period-seconds', type=int, default=15*60, required=False)
    monitor_parser.set_defaults(command='monitor')

    bot_test_parser = subparsers.add_parser('bot-test')
    bot_test_parser.set_defaults(command='bot-test')

    args = parser.parse_args()

    log_level = args.log_level.upper() if args.log_level else None
    log_level = log_level or 'INFO'

    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)
    coloredlogs.set_level(log_level)

    logger.info('parsed args: %s', args)

    if args.command == 'check':
        check_once(
            headless=args.headless,
        )
    elif args.command == 'monitor':
        monitor(
            period_seconds=args.period_seconds,
            headless=args.headless,
        )
    elif args.command == 'bot-test':
        bot_test(
            headless=args.headless,
        )
    else:
        raise RuntimeError('unknown command: %s' % args.command)
