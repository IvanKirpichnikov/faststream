import logging
from typing import TYPE_CHECKING, Any, Callable, ClassVar, Optional, Tuple, Union

from faststream._internal.broker.broker import BrokerUsecase
from faststream._internal.constants import EMPTY
from faststream._internal.log.logging import get_broker_logger
from faststream.confluent.client import AsyncConfluentConsumer

if TYPE_CHECKING:
    import confluent_kafka

    from faststream._internal.basic_types import LoggerProto


class KafkaLoggingBroker(
    BrokerUsecase[
        Union["confluent_kafka.Message", Tuple["confluent_kafka.Message", ...]],
        Callable[..., AsyncConfluentConsumer],
    ]
):
    """A class that extends the LoggingMixin class and adds additional functionality for logging Kafka related information."""

    _max_topic_len: int
    _max_group_len: int
    __max_msg_id_ln: ClassVar[int] = 10

    def __init__(
        self,
        *args: Any,
        logger: Optional["LoggerProto"] = EMPTY,
        log_level: int = logging.INFO,
        log_fmt: Optional[str] = None,
        **kwargs: Any,
    ) -> None:
        """Initialize the class."""
        super().__init__(
            *args,
            logger=logger,
            # TODO: generate unique logger names to not share between brokers
            default_logger=get_broker_logger(
                name="confluent",
                default_context={
                    "topic": "",
                    "group_id": "",
                },
                message_id_ln=self.__max_msg_id_ln,
            ),
            log_level=log_level,
            log_fmt=log_fmt,
            **kwargs,
        )
        self._max_topic_len = 4
        self._max_group_len = 0

    def get_fmt(self) -> str:
        return (
            "%(asctime)s %(levelname)-8s - "
            + f"%(topic)-{self._max_topic_len}s | "
            + (f"%(group_id)-{self._max_group_len}s | " if self._max_group_len else "")
            + f"%(message_id)-{self.__max_msg_id_ln}s "
            + "- %(message)s"
        )

    def _setup_log_context(
        self,
        *,
        topic: str = "",
        group_id: Optional[str] = None,
    ) -> None:
        """Set up log context."""
        self._max_topic_len = max((self._max_topic_len, len(topic)))
        self._max_group_len = max((self._max_group_len, len(group_id or "")))
