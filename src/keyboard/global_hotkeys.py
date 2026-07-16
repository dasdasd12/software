"""Windows global hotkeys reserved for physical keyboard actions."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import sys
import threading
from typing import Callable, Optional


class GlobalHotkeyUnavailable(RuntimeError):
    """Raised when a requested OS hotkey cannot be registered."""


class WindowsF24HotkeyListener:
    """Capture the keyboard firmware's reserved Claude control keys."""

    HOTKEY_ID_YES = 0x4B22
    HOTKEY_ID_NO = 0x4B23
    HOTKEY_ID = 0x4B24
    MOD_NOREPEAT = 0x4000
    VK_F22 = 0x85
    VK_F23 = 0x86
    VK_F24 = 0x87
    WM_HOTKEY = 0x0312
    WM_QUIT = 0x0012
    PM_NOREMOVE = 0x0000

    def __init__(
        self,
        on_claude_launch: Callable[[], None],
        on_approval_yes: Optional[Callable[[], None]] = None,
        on_approval_no: Optional[Callable[[], None]] = None,
        *,
        user32: Optional[object] = None,
        kernel32: Optional[object] = None,
        startup_timeout_sec: float = 2.0,
        stop_timeout_sec: float = 2.0,
    ) -> None:
        if user32 is None or kernel32 is None:
            if sys.platform != "win32":
                raise GlobalHotkeyUnavailable(
                    "the physical F24 shortcut is supported only on Windows"
                )
            user32 = user32 or ctypes.WinDLL("user32", use_last_error=True)
            kernel32 = kernel32 or ctypes.WinDLL(
                "kernel32", use_last_error=True
            )

        self._on_claude_launch = on_claude_launch
        self._on_approval_yes = on_approval_yes
        self._on_approval_no = on_approval_no
        self._user32 = user32
        self._kernel32 = kernel32
        self._configure_win32_signatures()
        self._startup_timeout_sec = max(0.01, float(startup_timeout_sec))
        self._stop_timeout_sec = max(0.01, float(stop_timeout_sec))
        self._state_lock = threading.RLock()
        self._thread: Optional[threading.Thread] = None
        self._thread_id = 0
        self._ready = threading.Event()
        self._stop_requested = threading.Event()
        self._startup_error: Optional[BaseException] = None

    def set_approval_callbacks(
        self,
        on_approval_yes: Optional[Callable[[], None]],
        on_approval_no: Optional[Callable[[], None]],
    ) -> None:
        with self._state_lock:
            if self._thread is not None and self._thread.is_alive():
                raise GlobalHotkeyUnavailable(
                    "approval callbacks must be configured before starting"
                )
            self._on_approval_yes = on_approval_yes
            self._on_approval_no = on_approval_no

    @property
    def is_running(self) -> bool:
        with self._state_lock:
            return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        with self._state_lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._thread = None
            self._thread_id = 0
            self._ready = threading.Event()
            self._stop_requested = threading.Event()
            self._startup_error = None
            thread = threading.Thread(
                target=self._run,
                name="ai-keyboard-f24-hotkey",
                daemon=True,
            )
            self._thread = thread

        thread.start()
        if not self._ready.wait(timeout=self._startup_timeout_sec):
            self._cancel_startup(thread)
            raise GlobalHotkeyUnavailable(
                "timed out while registering the Windows F24 hotkey"
            )
        with self._state_lock:
            startup_error = self._startup_error
        if startup_error is not None:
            thread.join(timeout=self._stop_timeout_sec)
            if not thread.is_alive():
                self._clear_thread(thread)
            raise GlobalHotkeyUnavailable(str(startup_error))
        if not thread.is_alive():
            self._clear_thread(thread)
            raise GlobalHotkeyUnavailable(
                "the Windows F24 hotkey listener exited during startup"
            )

    def stop(self) -> None:
        with self._state_lock:
            thread = self._thread
            thread_id = self._thread_id
            stop_requested = self._stop_requested
        if thread is None:
            return
        if thread is threading.current_thread():
            raise GlobalHotkeyUnavailable(
                "the F24 listener cannot stop itself"
            )

        stop_requested.set()
        if thread_id:
            posted = bool(
                self._user32.PostThreadMessageW(
                    thread_id, self.WM_QUIT, 0, 0
                )
            )
            if not posted:
                error = ctypes.get_last_error()
                thread.join(timeout=0)
                if thread.is_alive():
                    raise OSError(
                        error,
                        "Windows could not stop the F24 hotkey listener",
                    )

        thread.join(timeout=self._stop_timeout_sec)
        if thread.is_alive():
            raise GlobalHotkeyUnavailable(
                "timed out while stopping the Windows F24 hotkey listener"
            )
        self._clear_thread(thread)

    def _run(self) -> None:
        message = wintypes.MSG()
        registered_ids = []
        try:
            thread_id = int(self._kernel32.GetCurrentThreadId())
            with self._state_lock:
                self._thread_id = thread_id
            # Ensure this thread owns a Windows message queue before stop()
            # can post WM_QUIT to it.
            self._user32.PeekMessageW(
                ctypes.byref(message), None, 0, 0, self.PM_NOREMOVE
            )
            if self._stop_requested.is_set():
                self._set_startup_error(
                    GlobalHotkeyUnavailable(
                        "the Windows F24 hotkey startup was cancelled"
                    )
                )
                return
            hotkeys = [
                (self.HOTKEY_ID, self.VK_F24, self._on_claude_launch),
                (self.HOTKEY_ID_YES, self.VK_F22, self._on_approval_yes),
                (self.HOTKEY_ID_NO, self.VK_F23, self._on_approval_no),
            ]
            callbacks = {}
            for hotkey_id, virtual_key, callback in hotkeys:
                if callback is None:
                    continue
                registered = bool(
                    self._user32.RegisterHotKey(
                        None,
                        hotkey_id,
                        self.MOD_NOREPEAT,
                        virtual_key,
                    )
                )
                if not registered:
                    error = ctypes.get_last_error()
                    self._set_startup_error(
                        OSError(
                            error,
                            "Windows could not register the physical keyboard shortcuts",
                        )
                    )
                    return
                registered_ids.append(hotkey_id)
                callbacks[hotkey_id] = callback
            if self._stop_requested.is_set():
                self._set_startup_error(
                    GlobalHotkeyUnavailable(
                        "the Windows F24 hotkey startup was cancelled"
                    )
                )
                return
            self._ready.set()

            while True:
                result = int(
                    self._user32.GetMessageW(
                        ctypes.byref(message), None, 0, 0
                    )
                )
                if result == -1:
                    error = ctypes.get_last_error()
                    raise OSError(
                        error,
                        "Windows F24 hotkey message loop failed",
                    )
                if result == 0:
                    break
                if (
                    message.message == self.WM_HOTKEY
                ):
                    callback = callbacks.get(int(message.wParam))
                    if callback is None:
                        continue
                    try:
                        callback()
                    except Exception:
                        # The Local Core callback only schedules work. A
                        # callback failure must not kill the message loop.
                        continue
        except BaseException as exc:
            if not self._ready.is_set():
                self._set_startup_error(exc)
        finally:
            for hotkey_id in reversed(registered_ids):
                self._user32.UnregisterHotKey(None, hotkey_id)
            with self._state_lock:
                self._thread_id = 0
            self._ready.set()

    def _cancel_startup(self, thread: threading.Thread) -> None:
        self._stop_requested.set()
        with self._state_lock:
            thread_id = self._thread_id
        if thread_id:
            self._user32.PostThreadMessageW(
                thread_id, self.WM_QUIT, 0, 0
            )
        thread.join(timeout=self._stop_timeout_sec)
        if thread.is_alive():
            raise GlobalHotkeyUnavailable(
                "F24 registration timed out and listener cleanup did not finish"
            )
        self._clear_thread(thread)

    def _clear_thread(self, thread: threading.Thread) -> None:
        with self._state_lock:
            if self._thread is thread and not thread.is_alive():
                self._thread = None
                self._thread_id = 0

    def _set_startup_error(self, exc: BaseException) -> None:
        with self._state_lock:
            if self._startup_error is None:
                self._startup_error = exc

    def _configure_win32_signatures(self) -> None:
        self._set_signature(
            self._user32.RegisterHotKey,
            [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT],
            wintypes.BOOL,
        )
        self._set_signature(
            self._user32.UnregisterHotKey,
            [wintypes.HWND, ctypes.c_int],
            wintypes.BOOL,
        )
        self._set_signature(
            self._user32.PeekMessageW,
            [
                ctypes.POINTER(wintypes.MSG),
                wintypes.HWND,
                wintypes.UINT,
                wintypes.UINT,
                wintypes.UINT,
            ],
            wintypes.BOOL,
        )
        self._set_signature(
            self._user32.GetMessageW,
            [
                ctypes.POINTER(wintypes.MSG),
                wintypes.HWND,
                wintypes.UINT,
                wintypes.UINT,
            ],
            wintypes.BOOL,
        )
        self._set_signature(
            self._user32.PostThreadMessageW,
            [
                wintypes.DWORD,
                wintypes.UINT,
                wintypes.WPARAM,
                wintypes.LPARAM,
            ],
            wintypes.BOOL,
        )
        self._set_signature(
            self._kernel32.GetCurrentThreadId,
            [],
            wintypes.DWORD,
        )

    @staticmethod
    def _set_signature(function, argtypes, restype) -> None:
        if hasattr(function, "argtypes"):
            function.argtypes = argtypes
        if hasattr(function, "restype"):
            function.restype = restype
