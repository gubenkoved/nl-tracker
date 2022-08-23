import logging
import urllib.parse

from anticaptchaofficial.hcaptchaproxyless import *
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.common.by import By

logger = logging.getLogger(__name__)


def solve_captcha(driver: WebDriver, anticaptcha_api_key: str):
    logger.info('solving captcha with AntiCaptcha...')
    solver = hCaptchaProxyless()

    solver.set_website_url(driver.current_url)
    solver.set_key(anticaptcha_api_key)

    # TODO: make it more reliable! need to wait for iframes to be added
    time.sleep(5)

    captcha_iframe = driver.find_elements(By.TAG_NAME, 'iframe')[0]
    captcha_iframe_src = captcha_iframe.get_attribute('src')
    parsed_src = urllib.parse.urlparse(captcha_iframe_src)
    parsed_qs = urllib.parse.parse_qs(parsed_src.fragment)
    site_key = parsed_qs.get('sitekey')[0]
    solver.set_website_key(site_key)

    logger.info('submit the job for AntiCaptcha and wait for result...')
    solution = solver.solve_and_return_solution()

    if solution == 0:
        raise RuntimeError('AntiCaptcha failed!')

    logger.info('retrieved solution from AntiCaptcha: %s', solution)

    # insert the token into the iframe attribute
    logger.info('inserting the token into the iframe attributes')
    for iframe in driver.find_elements(By.TAG_NAME, 'iframe'):
        driver.execute_script(
            "arguments[0].setAttribute('data-hcaptcha-response',arguments[1])",
            iframe, solution)

    logger.info('inserting the token into the textarea')
    for textarea in driver.find_elements(By.XPATH, '//textarea[@name = "h-captcha-response"]'):
        driver.execute_script(
            "arguments[0].textContent = arguments[1]",
            textarea, solution)

    logger.info('executing callback...')
    driver.execute_script("window[hcaptchaHandle.callback](arguments[0])", solution)
