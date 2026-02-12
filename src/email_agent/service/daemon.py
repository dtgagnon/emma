"""Emma service daemon with APScheduler."""

import asyncio
import logging
import signal
from datetime import datetime
from pathlib import Path
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from ..config import Settings
from ..processors.llm import LLMProcessor, create_llm_client
from .action_items import ActionItemManager
from .digest import DigestGenerator
from .monitor import EmailMonitor
from .state import ServiceState

logger = logging.getLogger(__name__)


class EmmaService:
    """Background service daemon for email monitoring and automation."""

    def __init__(self, settings: Settings) -> None:
        """Initialize the Emma service.

        Args:
            settings: Application settings.
        """
        self.settings = settings
        self.scheduler = AsyncIOScheduler()
        self._running = False
        self._shutdown_event = asyncio.Event()

        # Initialize state manager
        self.state = ServiceState(settings.db_path)

        # Initialize LLM processor if configured
        self.llm_processor: LLMProcessor | None = None
        if settings.llm:
            try:
                api_key = settings.anthropic_api_key if settings.llm.provider == "anthropic" else None
                self.llm_processor = LLMProcessor(
                    settings.llm,
                    api_key,
                    user_email_lookup=settings.get_user_email_for_source,
                )
            except Exception as e:
                logger.warning(f"Could not initialize LLM processor: {e}")

        # Initialize action item manager
        self.action_manager = ActionItemManager(
            state=self.state,
            llm_processor=self.llm_processor,
            config=settings.service.action_items,
        )

        # Initialize email monitor
        self.monitor = EmailMonitor(
            settings=settings,
            state=self.state,
            config=settings.service.monitor,
            llm_processor=self.llm_processor,
            action_manager=self.action_manager,
        )

        # Initialize digest generator
        self.digest_generator = DigestGenerator(
            settings=settings,
            state=self.state,
            llm_processor=self.llm_processor,
        )

        # Track last run times
        self._last_monitor_run: datetime | None = None
        self._last_digest_run: datetime | None = None

    def _setup_jobs(self) -> None:
        """Set up scheduled jobs."""
        service_config = self.settings.service

        # Monitor job - runs at polling interval
        if service_config.monitor.enabled:
            interval = service_config.polling_interval
            self.scheduler.add_job(
                self._run_monitor_job,
                trigger=IntervalTrigger(seconds=interval),
                id="monitor",
                name="Email Monitor",
                replace_existing=True,
            )
            logger.info(f"Scheduled monitor job every {interval} seconds")

        # Digest jobs - runs at scheduled times
        if service_config.digest.enabled:
            for schedule_time in service_config.digest.schedule:
                try:
                    hour, minute = map(int, schedule_time.split(":"))
                    self.scheduler.add_job(
                        self._run_digest_job,
                        trigger=CronTrigger(hour=hour, minute=minute),
                        id=f"digest_{schedule_time}",
                        name=f"Digest at {schedule_time}",
                        replace_existing=True,
                    )
                    logger.info(f"Scheduled digest job at {schedule_time}")
                except ValueError as e:
                    logger.error(f"Invalid schedule time '{schedule_time}': {e}")

        # Cleanup job - runs daily at 3am
        self.scheduler.add_job(
            self._run_cleanup_job,
            trigger=CronTrigger(hour=3, minute=0),
            id="cleanup",
            name="Daily Cleanup",
            replace_existing=True,
        )

    async def _run_monitor_job(self) -> None:
        """Execute the monitoring job."""
        logger.debug("Running monitor job")
        try:
            stats = await self.monitor.run_cycle()
            self._last_monitor_run = datetime.now()
            logger.info(f"Monitor job complete: {stats}")
        except Exception as e:
            logger.error(f"Monitor job failed: {e}")

    async def _run_digest_job(self) -> None:
        """Execute the digest generation job."""
        logger.info("Running digest job")
        try:
            digest = await self.digest_generator.generate(
                period_hours=self.settings.service.digest.period_hours,
            )
            if digest:
                await self.digest_generator.deliver(digest)
                self._last_digest_run = datetime.now()
                logger.info(f"Digest generated and delivered: {digest.id}")
            else:
                logger.info("No digest generated (no new emails)")
        except Exception as e:
            logger.error(f"Digest job failed: {e}")

    async def _run_cleanup_job(self) -> None:
        """Execute the cleanup job."""
        logger.debug("Running cleanup job")
        try:
            deleted = self.state.cleanup_old_data(days=30)
            logger.info(f"Cleanup complete: {deleted}")
        except Exception as e:
            logger.error(f"Cleanup job failed: {e}")

    async def start(self) -> None:
        """Start the service daemon."""
        if self._running:
            logger.warning("Service is already running")
            return

        logger.info("Starting Emma service")
        self._running = True
        self._shutdown_event.clear()

        # Set up signal handlers
        loop = asyncio.get_event_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, lambda: asyncio.create_task(self.stop()))

        # Set up and start scheduler
        self._setup_jobs()
        self.scheduler.start()

        # Run initial monitor cycle
        if self.settings.service.monitor.enabled:
            await self._run_monitor_job()

        logger.info("Emma service started")

        # Wait for shutdown signal
        await self._shutdown_event.wait()

    async def stop(self) -> None:
        """Stop the service daemon gracefully."""
        if not self._running:
            return

        logger.info("Stopping Emma service")
        self._running = False

        # Shutdown scheduler
        if self.scheduler.running:
            self.scheduler.shutdown(wait=True)

        self._shutdown_event.set()
        logger.info("Emma service stopped")

    async def run_once(
        self,
        *,
        run_monitor: bool = True,
        run_digest: bool = False,
    ) -> dict[str, Any]:
        """Run service jobs once without starting the daemon.

        Args:
            run_monitor: Whether to run the monitoring cycle.
            run_digest: Whether to run digest generation.

        Returns:
            Dict with results from each job run.
        """
        results: dict[str, Any] = {}

        if run_monitor:
            results["monitor"] = await self.monitor.run_cycle()

        if run_digest:
            digest = await self.digest_generator.generate(
                period_hours=self.settings.service.digest.period_hours,
            )
            if digest:
                delivered = await self.digest_generator.deliver(digest)
                results["digest"] = {
                    "id": digest.id,
                    "email_count": digest.email_count,
                    "delivered": delivered,
                }
            else:
                results["digest"] = {"generated": False, "reason": "No new emails"}

        return results

    def get_status(self) -> dict[str, Any]:
        """Get the current service status.

        Returns:
            Dict with service status information.
        """
        status = {
            "running": self._running,
            "scheduler_running": self.scheduler.running if hasattr(self, "scheduler") else False,
            "last_monitor_run": self._last_monitor_run.isoformat() if self._last_monitor_run else None,
            "last_digest_run": self._last_digest_run.isoformat() if self._last_digest_run else None,
            "config": {
                "monitor_enabled": self.settings.service.monitor.enabled,
                "digest_enabled": self.settings.service.digest.enabled,
                "polling_interval": self.settings.service.polling_interval,
                "digest_schedule": self.settings.service.digest.schedule,
            },
        }

        # Add next job run times
        if self._running and self.scheduler.running:
            jobs = self.scheduler.get_jobs()
            status["next_jobs"] = {}
            for job in jobs:
                if job.next_run_time:
                    status["next_jobs"][job.id] = job.next_run_time.isoformat()

        # Add statistics from state
        status["stats"] = self.state.get_stats()

        return status
