from __future__ import annotations

import asyncio
import contextlib
import logging
import os

import aiohttp
from aiohttp import web

from .config import ConfigStore
from .control import HeatingController
from .ha_client import HomeAssistantClient
from .web import WebApplication


async def run() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    token = os.environ.get("SUPERVISOR_TOKEN")
    if not token:
        raise RuntimeError("SUPERVISOR_TOKEN is required")

    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        ha = HomeAssistantClient(
            base_url=os.environ.get("HA_BASE_URL", "http://supervisor/core"),
            token=token,
            session=session,
        )
        store = ConfigStore(os.environ.get("CONFIG_PATH", "/data/heat-steward-config.json"))
        controller = HeatingController(ha=ha, store=store)
        runner = web.AppRunner(WebApplication(ha=ha, store=store, controller=controller).create())
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", int(os.environ.get("PORT", "8099")))
        await site.start()
        controller_task = asyncio.create_task(controller.run_forever())
        mode_event_task = asyncio.create_task(
            ha.listen_events("heat_steward_set_mode", controller.handle_mode_event)
        )
        override_event_task = asyncio.create_task(
            ha.listen_events("heat_steward_override", controller.handle_override_event)
        )
        logging.getLogger(__name__).info("Heat Steward UI and controller started")
        try:
            await asyncio.gather(controller_task, mode_event_task, override_event_task)
        finally:
            for task in (controller_task, mode_event_task, override_event_task):
                if not task.done():
                    task.cancel()
            for task in (controller_task, mode_event_task, override_event_task):
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            await runner.cleanup()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
