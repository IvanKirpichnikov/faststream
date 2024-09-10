from typing import (
    TYPE_CHECKING,
    Any,
    Dict,
    Iterable,
    Literal,
    Optional,
    Tuple,
    Union,
    overload,
)

from typing_extensions import override

from faststream.broker.types import MsgType
from faststream.confluent.publisher.usecase import (
    BatchPublisher,
    DefaultPublisher,
    LogicPublisher,
)
from faststream.exceptions import SetupError
from faststream.specification.asyncapi.utils import resolve_payloads
from faststream.specification.schema.bindings import ChannelBinding, kafka
from faststream.specification.schema.channel import Channel
from faststream.specification.schema.message import CorrelationId, Message
from faststream.specification.schema.operation import Operation

if TYPE_CHECKING:
    from confluent_kafka import Message as ConfluentMsg

    from faststream.broker.types import BrokerMiddleware, PublisherMiddleware


class SpecificationPublisher(LogicPublisher[MsgType]):
    """A class representing a publisher."""

    def get_name(self) -> str:
        return f"{self.topic}:Publisher"

    def get_schema(self) -> Dict[str, Channel]:
        payloads = self.get_payloads()

        return {
            self.name: Channel(
                description=self.description,
                publish=Operation(
                    message=Message(
                        title=f"{self.name}:Message",
                        payload=resolve_payloads(payloads, "Publisher"),
                        correlationId=CorrelationId(
                            location="$message.header#/correlation_id"
                        ),
                    ),
                ),
                bindings=ChannelBinding(kafka=kafka.ChannelBinding(topic=self.topic)),
            )
        }

    @overload  # type: ignore[override]
    @staticmethod
    def create(
        *,
        batch: Literal[True],
        key: Optional[bytes],
        topic: str,
        partition: Optional[int],
        headers: Optional[Dict[str, str]],
        reply_to: str,
        # Publisher args
        broker_middlewares: Iterable["BrokerMiddleware[Tuple[ConfluentMsg, ...]]"],
        middlewares: Iterable["PublisherMiddleware"],
        # Specification args
        schema_: Optional[Any],
        title_: Optional[str],
        description_: Optional[str],
        include_in_schema: bool,
    ) -> "SpecificationBatchPublisher": ...

    @overload
    @staticmethod
    def create(
        *,
        batch: Literal[False],
        key: Optional[bytes],
        topic: str,
        partition: Optional[int],
        headers: Optional[Dict[str, str]],
        reply_to: str,
        # Publisher args
        broker_middlewares: Iterable["BrokerMiddleware[ConfluentMsg]"],
        middlewares: Iterable["PublisherMiddleware"],
        # Specification args
        schema_: Optional[Any],
        title_: Optional[str],
        description_: Optional[str],
        include_in_schema: bool,
    ) -> "SpecificationDefaultPublisher": ...

    @overload
    @staticmethod
    def create(
        *,
        batch: bool,
        key: Optional[bytes],
        topic: str,
        partition: Optional[int],
        headers: Optional[Dict[str, str]],
        reply_to: str,
        # Publisher args
        broker_middlewares: Iterable[
            "BrokerMiddleware[Union[Tuple[ConfluentMsg, ...], ConfluentMsg]]"
        ],
        middlewares: Iterable["PublisherMiddleware"],
        # Specification args
        schema_: Optional[Any],
        title_: Optional[str],
        description_: Optional[str],
        include_in_schema: bool,
    ) -> Union[
        "SpecificationBatchPublisher",
        "SpecificationDefaultPublisher",
    ]: ...

    @override
    @staticmethod
    def create(
        *,
        batch: bool,
        key: Optional[bytes],
        topic: str,
        partition: Optional[int],
        headers: Optional[Dict[str, str]],
        reply_to: str,
        # Publisher args
        broker_middlewares: Iterable[
            "BrokerMiddleware[Union[Tuple[ConfluentMsg, ...], ConfluentMsg]]"
        ],
        middlewares: Iterable["PublisherMiddleware"],
        # Specification args
        schema_: Optional[Any],
        title_: Optional[str],
        description_: Optional[str],
        include_in_schema: bool,
    ) -> Union[
        "SpecificationBatchPublisher",
        "SpecificationDefaultPublisher",
    ]:
        if batch:
            if key:
                raise SetupError("You can't setup `key` with batch publisher")

            return SpecificationBatchPublisher(
                topic=topic,
                partition=partition,
                headers=headers,
                reply_to=reply_to,
                broker_middlewares=broker_middlewares,
                middlewares=middlewares,
                schema_=schema_,
                title_=title_,
                description_=description_,
                include_in_schema=include_in_schema,
            )
        else:
            return SpecificationDefaultPublisher(
                key=key,
                # basic args
                topic=topic,
                partition=partition,
                headers=headers,
                reply_to=reply_to,
                broker_middlewares=broker_middlewares,
                middlewares=middlewares,
                schema_=schema_,
                title_=title_,
                description_=description_,
                include_in_schema=include_in_schema,
            )


class SpecificationBatchPublisher(
    BatchPublisher,
    SpecificationPublisher[Tuple["ConfluentMsg", ...]],
):
    pass


class SpecificationDefaultPublisher(
    DefaultPublisher,
    SpecificationPublisher["ConfluentMsg"],
):
    pass