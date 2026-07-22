import asyncio

from flowmate.scheduler.app import run_scheduler

if __name__ == "__main__":
    asyncio.run(run_scheduler())
