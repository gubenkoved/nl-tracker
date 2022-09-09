import logging
import subprocess
import threading
import time
from typing import BinaryIO, Optional

logger = logging.getLogger(__name__)


class ProxyHost:
    def __init__(self):
        self.proc: Optional[subprocess.Popen] = None
        self.log_file: Optional[BinaryIO] = None

    def start(self, port: int = 8080):
        """
        Starts mitmproxy and returns ports on which it is available.
        """
        if self.proc:
            raise Exception('already started!')

        self.log_file = open('mitmdump.log', 'ab')

        self.proc = subprocess.Popen([
            'mitmdump', '-s', 'proxy.py',
            '--anticache', '--listen-port', str(port)],
            stdout=self.log_file,
            stderr=self.log_file
        )

        self.monitor_thread = threading.Thread(target=self._monitor)
        self.monitor_thread.setDaemon(True)
        self.monitor_thread.start()

        logger.info('mimtproxy started!')

    def _monitor(self):
        while True:
            proc = self.proc

            if proc is None:
                break

            proc.poll()

            if proc.returncode is not None:
                logger.error(
                    'mitmdump has existed with exit code: %s (logs at %s)',
                    proc.returncode, self.log_file.name
                )
                break

            time.sleep(1)

    def stop(self):
        if self.proc is None:
            logger.warning('stop request issued, but proxy is not running')
            return

        logger.info('stopping mitmproxy...')

        self.proc.kill()
        self.proc = None
        self.log_file.close()
        self.log_file = None
        logger.info('stopped')
