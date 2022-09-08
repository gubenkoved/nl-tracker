# Installation

1. Install Python3 (confirmed to work on `3.8.9` and `3.10.4` on MacOS, Windows and Armbian)
2. `pip install -r requirements.txt`
3. Fill the `config.json` (you can create a copy of file before filling the values as `local.config.json` for easier updates from git to avoid conflicts)

## Telegram setup

1. Create bot via @BotFather, remember the API token for the bot
2. Create a channel
3. Add bot as an admin of the channel

# Captcha

Since end of August hCaptcha was introduced. In order to automatically work around that https://anti-captcha.com/ service is used.

However, solving captcha is not enough.

Firstly, modern browsers self-identify themselves via `navigator.webDriver` and let captcha services prevent automatic access.
To work around this we have to use old browser builds. For Firefox it is confirmed that `78.9.0esr` version works.

Secondly, browser runs via man-in-the-middle proxy (`mitmproxy`) in order to be able to intercept the JS callback which
site normally calls to pass the captcha token to the server. Otherwise, it's from very hard to impossible to do.