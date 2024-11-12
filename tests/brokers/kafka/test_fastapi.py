import asyncio
from typing import Any
from unittest.mock import Mock

import pytest

from faststream.kafka import KafkaBroker, KafkaRouter, TestKafkaBroker
from faststream.kafka.fastapi import KafkaRouter as StreamRouter
from tests.brokers.base.fastapi import FastAPILocalTestcase, FastAPITestcase


@pytest.mark.kafka()
class TestKafkaRouter(FastAPITestcase):
    router_class = StreamRouter
    broker_router_class = KafkaRouter

    async def test_batch_real(
        self,
        mock: Mock,
        queue: str,
    ) -> None:
        event = asyncio.Event()

        router = self.router_class()

        @router.subscriber(queue, batch=True)
        async def hello(msg: list[str]):
            event.set()
            return mock(msg)

        async with router.broker:
            await router.broker.start()
            await asyncio.wait(
                (
                    asyncio.create_task(router.broker.publish("hi", queue)),
                    asyncio.create_task(event.wait()),
                ),
                timeout=3,
            )

        assert event.is_set()
        mock.assert_called_with(["hi"])


class TestRouterLocal(FastAPILocalTestcase):
    router_class = StreamRouter
    broker_router_class = KafkaRouter

    def patch_broker(self, broker: KafkaBroker, **kwargs: Any) -> TestKafkaBroker:
        return TestKafkaBroker(broker, **kwargs)

    async def test_batch_testclient(
        self,
        mock: Mock,
        queue: str,
    ) -> None:
        event = asyncio.Event()

        router = self.router_class()

        @router.subscriber(queue, batch=True)
        async def hello(msg: list[str]):
            event.set()
            return mock(msg)

        async with self.patch_broker(router.broker) as br:
            await asyncio.wait(
                (
                    asyncio.create_task(br.publish("hi", queue)),
                    asyncio.create_task(event.wait()),
                ),
                timeout=3,
            )

        assert event.is_set()
        mock.assert_called_with(["hi"])
