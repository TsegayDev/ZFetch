import os
import shutil
import time
import logging
import threading
from django.apps import AppConfig
from django.conf import settings

logger = logging.getLogger('downloads')

_scheduler_started = False
_scheduler_lock = threading.Lock()


class DownloadsConfig(AppConfig):
    name = 'apps.downloads'
    label = 'downloads'

    def ready(self):
        """
        Called once when Django has finished loading all apps.
        Starts a daemon thread to periodically clean up expired temporary download directories
        without relying on database status checks or external tasks.
        """
        global _scheduler_started

        with _scheduler_lock:
            if _scheduler_started:
                return
            _scheduler_started = True

        thread = threading.Thread(target=self._run_cleanup_scheduler, daemon=True)
        thread.name = 'zfetch-temp-cleanup'
        thread.start()
        logger.info("[Scheduler] Stateless temp file cleanup scheduler started (5m interval).")

    @staticmethod
    def _run_cleanup_scheduler():
        """
        Infinite loop waking up every 5 minutes to purge temp folders older than 30 minutes.
        """
        INTERVAL_SECONDS = 300  # 5 minutes
        MAX_AGE_SECONDS = 1800  # 30 minutes

        while True:
            time.sleep(INTERVAL_SECONDS)
            temp_root = getattr(settings, 'TEMP_DOWNLOADS_ROOT', None)
            if not temp_root or not os.path.exists(temp_root):
                continue

            try:
                now = time.time()
                for name in os.listdir(temp_root):
                    path = os.path.join(temp_root, name)
                    try:
                        mtime = os.path.getmtime(path)
                        if now - mtime > MAX_AGE_SECONDS:
                            if os.path.isdir(path):
                                shutil.rmtree(path)
                            else:
                                os.remove(path)
                            logger.info(f"[Scheduler] Purged expired temp path: {path}")
                    except Exception as e:
                        logger.error(f"[Scheduler] Error purging {path}: {e}")
            except Exception as exc:
                logger.error(f"[Scheduler] Error scanning temp root: {exc}", exc_info=True)
