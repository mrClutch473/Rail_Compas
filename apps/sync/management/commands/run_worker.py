import logging
import signal
import time

from django.core.management.base import BaseCommand

log = logging.getLogger("worker")


class Command(BaseCommand):
    help = "Background worker: takes sync jobs from the queue and runs scrapers."

    def add_arguments(self, parser):
        parser.add_argument("--idle-sleep", type=float, default=2.0)

    def handle(self, *args, idle_sleep: float, **options):
        self.stopping = False
        signal.signal(signal.SIGTERM, self._stop)
        signal.signal(signal.SIGINT, self._stop)

        log.info("worker started")
        while not self.stopping:
            # The job queue arrives in phase 6; for now the worker only proves it is alive.
            time.sleep(idle_sleep)
        log.info("worker stopped")

    def _stop(self, signum, frame):
        log.info("got signal %s, finishing", signum)
        self.stopping = True
