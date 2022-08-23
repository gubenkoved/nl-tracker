import logging
import os
import urllib.parse

from mitmproxy import ctx
from mitmproxy import http

logger = logging.getLogger(__name__)
script_path = os.path.join(
    os.path.dirname(__file__),
    'captcha',
    'injection.js'
)


class DebugAddon:
    def request(self, flow: http.HTTPFlow):
        ctx.log.info('path: %s' % flow.request.path)
        ctx.log.info('url: %s' % flow.request.url)
        ctx.log.info('parsed: %s' % (urllib.parse.urlparse(flow.request.url),))


class InjectScript:
    def __init__(self):
        ctx.log.info('loading script from %s' % script_path)
        with open(script_path, 'r') as f:
            self.script = f.read().encode('utf-8')

    def response(self, flow: http.HTTPFlow):
        if flow.response and flow.response.content:
            parsed_url = urllib.parse.urlparse(flow.request.url)
            if parsed_url.path.endswith('AppWelcome.aspx'):
                ctx.log.info('injecting script...')
                flow.response.headers['X-Injected'] = 'Yes'
                flow.response.content = flow.response.content.replace(
                    b"</head>",
                    b"<script>\n" + self.script + b"</script>" + b"</head>"
                )


addons = [DebugAddon(), InjectScript()]
