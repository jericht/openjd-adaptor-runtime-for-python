# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.

import re as _re
import threading as _threading
from abc import abstractmethod
from typing import List as _List, Optional as _Optional, TypeVar

from .configuration import AdaptorConfiguration
from ._base_adaptor import BaseAdaptor

__all__ = ["Adaptor"]

_T = TypeVar("_T", bound=AdaptorConfiguration)


class Adaptor(BaseAdaptor[_T]):
    """An Adaptor.

    Derived classes must override the on_run method, and may also optionally
    override the on_start, on_end, on_cleanup, and on_cancel methods.
    """

    # ===============================================
    #  Callbacks / virtual functions.
    # ===============================================

    def on_start(self):  # pragma: no cover
        """
        For job stickiness. Will start everything required for the Task. Will be used for all
        SubTasks.
        """
        pass

    @abstractmethod
    def on_run(self, run_data: dict):  # pragma: no cover
        """
        This will run for every task and will setup everything needed to render (including calling
        any managed processes). This will be overridden and defined in each advanced plugin.
        """
        pass

    def on_stop(self):  # pragma: no cover
        """
        For job stickiness. Will stop everything required for the Task before moving on to a new
        Task.
        """
        pass

    def on_cleanup(self):  # pragma: no cover
        """
        This callback will be any additional cleanup required by the adaptor.
        """
        pass

    # ===============================================
    # ===============================================

    def _start(self):  # pragma: no cover
        self.on_start()

    def _run(self, run_data: dict):
        """
        :param run_data: This is the data that changes between the different Tasks. Eg. frame
        number.
        """
        self.on_run(run_data)

    def _stop(self):  # pragma: no cover
        self.on_stop()

    def _cleanup(self):  # pragma: no cover
        self.on_cleanup()

    # -----------------------------------------------------------
    # run_script support (runtime-provided default implementation)
    # -----------------------------------------------------------

    #: Set by the default on_run_script before enqueuing work; cleared by the
    #: completion regex callback (see _get_run_script_regex_callbacks).
    _run_script_done: _Optional[_threading.Event] = None
    _run_script_error: _Optional[str] = None

    _RUN_SCRIPT_TIMEOUT_SECONDS: float = 3600.0

    def _get_run_script_regex_callbacks(self) -> list:
        """Returns the RegexCallback list that routes run_script completion
        sentinels from DCC stdout to this adaptor's state.

        Concrete adaptors should append the result of this method to their
        existing regex callback list during on_start (or equivalent), e.g.:

            callback_list.extend(self._get_run_script_regex_callbacks())

        Returns:
            list: A list of RegexCallback entries. Return type is `list`
                rather than `List[RegexCallback]` to keep the import of
                RegexCallback local and avoid package-level import cycles.
        """
        from ..app_handlers import RegexCallback  # local import to avoid cycles

        return [
            RegexCallback(
                [_re.compile(r"^openjd_run_script_complete: (.*)$")],
                self._handle_run_script_complete,
            ),
            RegexCallback(
                [_re.compile(r"^openjd_run_script_error: (.*)$")],
                self._handle_run_script_error,
            ),
        ]

    def _handle_run_script_complete(self, match) -> None:
        """Regex callback: run_script finished successfully."""
        if self._run_script_done is not None:
            self._run_script_done.set()

    def _handle_run_script_error(self, match) -> None:
        """Regex callback: run_script raised inside the DCC."""
        self._run_script_error = match.group(1) if match.lastindex else "unknown script"
        if self._run_script_done is not None:
            self._run_script_done.set()

    def on_run_script(self, script_file: str, script_args: dict) -> None:
        """Default implementation: enqueue Action('__openjd_run_script__', ...)
        on the adaptor's action queue and wait for the DCC to print a
        completion sentinel on stdout.

        Concrete adaptors that use the ActionsQueue pattern (which is the
        canonical way to drive a DCC client) inherit this implementation with
        minimal code. The requirements are:

        1. The adaptor exposes an `_action_queue: ActionsQueue` attribute.
        2. The adaptor appends `self._get_run_script_regex_callbacks()` to the
           callback list it registers with its RegexHandler during on_start.

        Args:
            script_file (str): Absolute path to a .py file inside the DCC host.
            script_args (dict): Arbitrary JSON-serializable args; exposed to
                the script as a top-level `script_args` global.
        """
        # Local imports to avoid package-level cycles.
        from ..application_ipc import ActionsQueue
        from openjd.adaptor_runtime_client import Action

        action_queue = getattr(self, "_action_queue", None)
        if not isinstance(action_queue, ActionsQueue):
            raise RuntimeError(
                f"{type(self).__name__}.on_run_script requires a "
                "'_action_queue: ActionsQueue' attribute. Either set one in "
                "the concrete adaptor or override on_run_script."
            )

        self._run_script_done = _threading.Event()
        self._run_script_error = None

        action_queue.enqueue_action(
            Action(
                "__openjd_run_script__",
                {"script_file": script_file, "script_args": script_args or {}},
            )
        )

        if not self._run_script_done.wait(timeout=self._RUN_SCRIPT_TIMEOUT_SECONDS):
            raise TimeoutError(
                f"run_script did not complete within "
                f"{self._RUN_SCRIPT_TIMEOUT_SECONDS} seconds: {script_file}"
            )

        if self._run_script_error:
            raise RuntimeError(
                f"run_script raised inside the DCC: {self._run_script_error}"
            )

    def _run_script(self, script_file: str, script_args: dict) -> None:
        """Internal dispatch invoked by AdaptorRunner._run_script; calls on_run_script."""
        self.on_run_script(script_file, script_args)
