import asyncio
import logging
import signal

from django.core.management.base import BaseCommand

from apps.sync.worker import Worker

log = logging.getLogger("worker")


class Command(BaseCommand):
    help = "Background worker: takes sync jobs from the queue and runs the scrapers."

    def add_arguments(self, parser):
        parser.add_argument("--slots", type=int, default=None, help="jobs processed at once")
        parser.add_argument("--idle-sleep", type=float, default=1.0)
        parser.add_argument("--until-empty", action="store_true", help="exit when queue is empty")

    def handle(self, *args, slots, idle_sleep, until_empty, **options):
        logging.getLogger("httpx").setLevel(logging.WARNING)
        worker = Worker(slots=slots, idle_sleep=idle_sleep)

        def stop(signum, frame):
            log.info("got signal %s, finishing current jobs", signum)
            worker.stop()

        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        asyncio.run(worker.run(until_empty=until_empty))
