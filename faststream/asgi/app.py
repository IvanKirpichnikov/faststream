import logging
import traceback
from abc import abstractmethod
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any, Optional, Protocol

import anyio

from faststream._internal._compat import HAS_TYPER, ExceptionGroup
from faststream._internal.application import Application
from faststream._internal.constants import EMPTY
from faststream._internal.log import logger
from faststream.asgi.response import AsgiResponse
from faststream.asgi.websocket import WebSocketClose
from faststream.exceptions import StartupValidationError

if TYPE_CHECKING:
    from types import FrameType

    from anyio.abc import TaskStatus
    from fast_depends import Provider
    from fast_depends.library.serializer import SerializerProto

    from faststream._internal.basic_types import (
        AnyCallable,
        AnyDict,
        Lifespan,
        LoggerProto,
        SettingField,
    )
    from faststream._internal.broker.broker import BrokerUsecase
    from faststream.asgi.types import ASGIApp, Receive, Scope, Send

    class UvicornServerProtocol(Protocol):
        should_exit: bool
        force_exit: bool

        def handle_exit(self, sig: int, frame: Optional[FrameType]) -> None: ...


class ServerState(Protocol):
    extra_options: dict[str, "SettingField"]

    @abstractmethod
    def stop(self) -> None: ...


class OuterRunState(ServerState):
    def __init__(self) -> None:
        self.extra_options = {}

    def stop(self) -> None:
        # TODO: resend signal to outer uvicorn
        pass


class CliRunState(ServerState):
    def __init__(
        self,
        server: "UvicornServerProtocol",
        extra_options: dict[str, "SettingField"],
    ) -> None:
        self.server = server
        self.extra_options = extra_options

    def stop(self) -> None:
        self.server.should_exit = True


class AsgiFastStream(Application):
    _server: ServerState

    def __init__(
        self,
        broker: "BrokerUsecase[Any, Any]",
        /,
        asgi_routes: Sequence[tuple[str, "ASGIApp"]] = (),
        # regular broker args
        logger: Optional["LoggerProto"] = logger,
        provider: Optional["Provider"] = None,
        serializer: Optional["SerializerProto"] = EMPTY,
        lifespan: Optional["Lifespan"] = None,
        # hooks
        on_startup: Sequence["AnyCallable"] = (),
        after_startup: Sequence["AnyCallable"] = (),
        on_shutdown: Sequence["AnyCallable"] = (),
        after_shutdown: Sequence["AnyCallable"] = (),
    ) -> None:
        super().__init__(
            broker,
            logger=logger,
            provider=provider,
            serializer=serializer,
            lifespan=lifespan,
            on_startup=on_startup,
            after_startup=after_startup,
            on_shutdown=on_shutdown,
            after_shutdown=after_shutdown,
        )

        self.routes = list(asgi_routes)

        self._server = OuterRunState()

    @classmethod
    def from_app(
        cls,
        app: Application,
        asgi_routes: Sequence[tuple[str, "ASGIApp"]],
    ) -> "AsgiFastStream":
        asgi_app = cls(
            app.broker,
            asgi_routes=asgi_routes,
            logger=app.logger,
            lifespan=None,
        )
        asgi_app.lifespan_context = app.lifespan_context
        asgi_app._on_startup_calling = app._on_startup_calling
        asgi_app._after_startup_calling = app._after_startup_calling
        asgi_app._on_shutdown_calling = app._on_shutdown_calling
        asgi_app._after_shutdown_calling = app._after_shutdown_calling
        return asgi_app

    def mount(self, path: str, route: "ASGIApp") -> None:
        self.routes.append((path, route))

    async def __call__(
        self,
        scope: "Scope",
        receive: "Receive",
        send: "Send",
    ) -> None:
        """ASGI implementation."""
        if scope["type"] == "lifespan":
            await self.lifespan(scope, receive, send)
            return

        if scope["type"] == "http":
            for path, app in self.routes:
                if scope["path"] == path:
                    await app(scope, receive, send)
                    return

        await self.not_found(scope, receive, send)
        return

    async def run(
        self,
        log_level: int = logging.INFO,
        run_extra_options: Optional[dict[str, "SettingField"]] = None,
    ) -> None:
        try:
            import uvicorn  # noqa: F401
            from gunicorn.app.base import BaseApplication
        except ImportError as e:
            msg = "You need uvicorn and gunicorn to run FastStream ASGI App via CLI. pip install uvicorn gunicorn"
            raise RuntimeError(msg) from e

        class ASGIRunner(BaseApplication):  # type: ignore[misc]
            def __init__(self, options: "AnyDict", asgi_app: "ASGIApp") -> None:
                self.options = options
                self.asgi_app = asgi_app
                super().__init__()

            def load_config(self) -> None:
                for k, v in self.options.items():
                    if k in self.cfg.settings and v is not None:
                        self.cfg.set(k.lower(), v)

            def load(self) -> "ASGIApp":
                return self.asgi_app

        run_extra_options = run_extra_options or {}

        bindings: list[str] = []
        host = run_extra_options.pop("host", None)
        port = run_extra_options.pop("port", None)
        if host is not None and port is not None:
            bindings.append(f"{host}:{port}")
        elif host is not None:
            bindings.append(f"{host}:8000")
        elif port is not None:
            bindings.append(f"127.0.0.1:{port}")

        run_extra_options["workers"] = int(run_extra_options.pop("workers", 1))

        bind = run_extra_options.get("bind")
        if isinstance(bind, list):
            bindings.extend(bind)
        elif isinstance(bind, str):
            bindings.append(bind)

        run_extra_options["bind"] = bindings or "127.0.0.1:8000"
        #  We use gunicorn with uvicorn workers because uvicorn don't support multiple workers
        run_extra_options["worker_class"] = "uvicorn.workers.UvicornWorker"

        ASGIRunner(run_extra_options, self).run()

    def exit(self) -> None:
        """Manual stop method."""
        self._server.stop()

    @asynccontextmanager
    async def start_lifespan_context(
        self,
        run_extra_options: Optional[dict[str, "SettingField"]] = None,
    ) -> AsyncIterator[None]:
        run_extra_options = run_extra_options or self._server.extra_options

        async with self.lifespan_context(**run_extra_options):
            try:
                async with anyio.create_task_group() as tg:
                    await tg.start(self.__start, logging.INFO, run_extra_options)

                    try:
                        yield
                    finally:
                        await self._shutdown()
                        tg.cancel_scope.cancel()

            except ExceptionGroup as e:
                for ex in e.exceptions:
                    raise ex from None

    async def __start(
        self,
        log_level: int,
        run_extra_options: dict[str, "SettingField"],
        *,
        task_status: "TaskStatus[None]" = anyio.TASK_STATUS_IGNORED,
    ) -> None:
        """Redefenition of `_startup` method.

        Waits for hooks run before broker start.
        """
        async with (
            self._startup_logging(log_level=log_level),
            self._start_hooks_context(**run_extra_options),
        ):
            task_status.started()
            await self._start_broker()

    async def lifespan(self, scope: "Scope", receive: "Receive", send: "Send") -> None:
        """Handle ASGI lifespan messages to start and shutdown the app."""
        started = False
        await receive()  # handle `lifespan.startup` event

        async def process_exception(ex: BaseException) -> None:
            exc_text = traceback.format_exc()
            if started:
                await send({"type": "lifespan.shutdown.failed", "message": exc_text})
            else:
                await send({"type": "lifespan.startup.failed", "message": exc_text})
            raise ex

        try:
            async with self.start_lifespan_context():
                await send({"type": "lifespan.startup.complete"})
                started = True
                await receive()  # handle `lifespan.shutdown` event

        except StartupValidationError as startup_exc:
            # Process `on_startup` and `lifespan` missed extra options
            if HAS_TYPER:
                from faststream._internal.cli.utils.errors import draw_startup_errors

                draw_startup_errors(startup_exc)
                await send({"type": "lifespan.startup.failed", "message": ""})

            else:
                await process_exception(startup_exc)

        except BaseException as base_exc:
            await process_exception(base_exc)

        else:
            await send({"type": "lifespan.shutdown.complete"})

    async def not_found(self, scope: "Scope", receive: "Receive", send: "Send") -> None:
        not_found_msg = "Application doesn't support regular HTTP protocol."

        if scope["type"] == "websocket":
            websocket_close = WebSocketClose(
                code=1000,
                reason=not_found_msg,
            )
            await websocket_close(scope, receive, send)
            return

        response = AsgiResponse(
            body=not_found_msg.encode(),
            status_code=404,
        )

        await response(scope, receive, send)
