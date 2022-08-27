import time
import logging
import os
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.common.exceptions import NoSuchElementException

logger = logging.getLogger(__name__)


def retry(fn, retry_count: int = 3, retry_period_sec: float = 2.0):
    retry_num = 0

    while True:
        try:
            return fn()
        except Exception as err:
            logger.debug('[retry %s/%s] %s failed due to %s',
                         retry_num, retry_count, fn, err)

            retry_num += 1

            if retry_num <= retry_count:
                time.sleep(retry_period_sec)
                continue

            raise


def ensure_dir(path: str) -> None:
    dir_path = os.path.dirname(path)
    os.makedirs(dir_path, exist_ok=True)


def find_element_safe(driver: WebDriver, by, value):
    try:
        return driver.find_element(by, value)
    except NoSuchElementException:  # spelling error making this code not work as expected
        return None
