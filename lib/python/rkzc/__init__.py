"""rkzc: librkzc's detector from Python.

Hardware decode, letterbox on the RGA and inference on the NPU of an RK3588, with no CPU copy of the
pixels; this module hands out the detections. It calls librkzc's C interface (rkzc_c.h) through ctypes and
needs nothing outside the standard library.

    import rkzc

    def on_frame(frame):
        for d in frame.detections:
            print(frame.channel, d.name, round(d.score, 2), d.x, d.y, d.w, d.h)

    with rkzc.Detector(["rtsp://camera/stream"], "yolov8n_i8.rknn", on_frame=on_frame) as det:
        det.start()
        det.wait(60)        # seconds; Ctrl-C works while waiting

The library is looked for as $RKZC_LIBRARY, then as librkzc.so.0 on the loader's path.
"""
import atexit
import collections
import ctypes
import enum
import os
import threading
import time
import traceback

__all__ = [
    "ChannelStats", "Detection", "Detector", "Error", "Event", "EventKind", "Frame", "ModelInfo", "SourceInfo",
    "check_model", "check_source", "class_name", "version",
]


class Error(RuntimeError):
    """A call into librkzc failed; the text is the library's reason."""


def _load():
    name = os.environ.get("RKZC_LIBRARY") or "librkzc.so.0"
    try:
        return ctypes.CDLL(name)
    except OSError as e:
        raise ImportError(
            f"rkzc: cannot load {name} ({e}); set RKZC_LIBRARY to the path of librkzc.so.0") from e


# ---- the C declarations (lib/include/rkzc/rkzc_c.h) ----

class _CDetection(ctypes.Structure):
    _fields_ = [("x", ctypes.c_float), ("y", ctypes.c_float), ("w", ctypes.c_float), ("h", ctypes.c_float),
                ("score", ctypes.c_float), ("cls", ctypes.c_int32)]


class _CFrame(ctypes.Structure):
    _fields_ = [("channel", ctypes.c_int32), ("width", ctypes.c_int32), ("height", ctypes.c_int32),
                ("count", ctypes.c_int32), ("seq", ctypes.c_int64), ("pts_us", ctypes.c_int64),
                ("capture_ns", ctypes.c_int64), ("detections", ctypes.POINTER(_CDetection))]


class _CEvent(ctypes.Structure):
    _fields_ = [("kind", ctypes.c_int32), ("channel", ctypes.c_int32), ("message", ctypes.c_char_p)]


class _CConfig(ctypes.Structure):
    _fields_ = [("sources", ctypes.POINTER(ctypes.c_char_p)), ("source_count", ctypes.c_int32),
                ("model", ctypes.c_char_p), ("post", ctypes.c_char_p), ("every_nth_frame", ctypes.c_int32),
                ("loop_files", ctypes.c_int32), ("reconnect_initial_ms", ctypes.c_int32),
                ("reconnect_max_ms", ctypes.c_int32), ("stall_timeout_ms", ctypes.c_int32)]


class _CStats(ctypes.Structure):
    _fields_ = [("decoded", ctypes.c_int64), ("sampled", ctypes.c_int64), ("inferred", ctypes.c_int64),
                ("dropped", ctypes.c_int64), ("errors", ctypes.c_int64), ("decode_errors", ctypes.c_int64),
                ("reconnects", ctypes.c_int64), ("connected", ctypes.c_int32), ("cached_buffers", ctypes.c_int32)]


class _CModelInfo(ctypes.Structure):
    _fields_ = [("width", ctypes.c_int32), ("height", ctypes.c_int32), ("outputs", ctypes.c_int32),
                ("runtime", ctypes.c_char * 128)]


class _CSourceInfo(ctypes.Structure):
    _fields_ = [("width", ctypes.c_int32), ("height", ctypes.c_int32)]


_FrameFn = ctypes.CFUNCTYPE(None, ctypes.POINTER(_CFrame), ctypes.c_void_p)
_EventFn = ctypes.CFUNCTYPE(None, ctypes.POINTER(_CEvent), ctypes.c_void_p)

_lib = _load()


def _declare(name, restype, *argtypes):
    f = getattr(_lib, name)
    f.restype = restype
    f.argtypes = list(argtypes)
    return f


_c, _vp, _i32, _sz = ctypes.c_char_p, ctypes.c_void_p, ctypes.c_int32, ctypes.c_size_t
_version = _declare("rkzc_version", _c)
_class_name = _declare("rkzc_class_name", _c, _i32)
_config_init = _declare("rkzc_config_init", None, ctypes.POINTER(_CConfig))
_open = _declare("rkzc_open", _vp, ctypes.POINTER(_CConfig), _c, _sz)
_close = _declare("rkzc_close", None, _vp)
_on_frame = _declare("rkzc_on_frame", None, _vp, _FrameFn, _vp)
_on_event = _declare("rkzc_on_event", None, _vp, _EventFn, _vp)
_start = _declare("rkzc_start", ctypes.c_int, _vp, _c, _sz)
_stop = _declare("rkzc_stop", None, _vp)
_wait = _declare("rkzc_wait", ctypes.c_int, _vp, _i32)
_channel_count = _declare("rkzc_channel_count", _i32, _vp)
_stats = _declare("rkzc_stats", _i32, _vp, ctypes.POINTER(_CStats), _i32)
_model_width = _declare("rkzc_model_width", _i32, _vp)
_model_height = _declare("rkzc_model_height", _i32, _vp)
_check_model = _declare("rkzc_check_model", ctypes.c_int, _c, ctypes.POINTER(_CModelInfo), _c, _sz)
_check_source = _declare("rkzc_check_source", ctypes.c_int, _c, ctypes.POINTER(_CSourceInfo), _i32, _c, _sz)

_ERROR_SIZE = 512


# ---- what the module hands out ----

class EventKind(enum.IntEnum):
    SOURCE_OPENED = 0  # the input and its decoder are open; also after a reconnection
    SOURCE_LOST = 1    # a stream ended, failed or gave no frame for the stall limit; it is opened again
    SOURCE_ENDED = 2   # a file reached its end (loop_files off); the channel is finished
    SOURCE_ERROR = 3   # the input could not be opened; it is tried again
    FRAME_ERROR = 4    # one frame failed in preprocessing or inference; it was skipped


class Detection(collections.namedtuple("Detection", "x y w h score cls")):
    """A box in the source frame's pixels (x, y: its top left corner), its score and its class index."""
    __slots__ = ()

    @property
    def name(self):
        return class_name(self.cls)


Frame = collections.namedtuple("Frame", "channel seq pts_us capture_ns width height detections")
Frame.__doc__ = """One inferred frame: its channel (index into the sources), its number in decode order,
the stream's timestamp in microseconds (-1: none), CLOCK_MONOTONIC in ns when it left the decoder, the
source frame's size, and a list of Detection."""
Event = collections.namedtuple("Event", "kind channel message")
ChannelStats = collections.namedtuple(
    "ChannelStats", "decoded sampled inferred dropped errors decode_errors reconnects connected cached_buffers")
ModelInfo = collections.namedtuple("ModelInfo", "width height outputs runtime")
SourceInfo = collections.namedtuple("SourceInfo", "width height")


def version():
    return _version().decode()


def class_name(cls):
    """COCO's name for a class index of the YOLO models, "?" outside 0..79."""
    return _class_name(cls).decode()


def check_model(model):
    """Loads the model the way the detector does and frees it. Raises Error with the reason if it cannot."""
    info, err = _CModelInfo(), ctypes.create_string_buffer(_ERROR_SIZE)
    if not _check_model(os.fsencode(model), ctypes.byref(info), err, _ERROR_SIZE):
        raise Error(err.value.decode(errors="replace"))
    return ModelInfo(info.width, info.height, info.outputs, info.runtime.decode(errors="replace"))


def check_source(source, timeout=10):
    """Opens an H.264 input with the hardware decoder and decodes one frame, within `timeout` seconds."""
    info, err = _CSourceInfo(), ctypes.create_string_buffer(_ERROR_SIZE)
    if not _check_source(os.fsencode(source), ctypes.byref(info), int(timeout), err, _ERROR_SIZE):
        raise Error(err.value.decode(errors="replace"))
    return SourceInfo(info.width, info.height)


# Started detectors, kept alive here until they are stopped: the garbage collector can run in any thread,
# and a running detector freed from its own callback thread would wait for itself. At exit the ones still
# running are closed, so that no callback arrives in an interpreter that is shutting down.
_live = set()
_live_lock = threading.Lock()
_in_callback = threading.local()


def _close_all():
    with _live_lock:
        running = list(_live)
    for d in running:
        d.close()


atexit.register(_close_all)


class Detector:
    """Decodes the sources, runs the model on one frame in every_nth_frame and calls on_frame(Frame).

    sources: a list of rtsp:// URLs or H.264 files, one per channel; model: a .rknn file. The options
    left as None take the library's defaults (rkzc.h's Config): every_nth_frame 6, post "auto",
    loop_files off, reconnect_initial_ms 1000, reconnect_max_ms 30000, stall_timeout_ms 10000.

    on_frame(Frame) and on_event(Event) are called from the library's threads, one at a time, each call
    holding Python's lock for as long as it runs: a slow callback holds the pipeline back and frames are
    dropped (stats() counts them). They can be set or replaced at any time. An exception in a callback is
    printed and counted in callback_errors; the detector goes on. stop() and close() cannot be called
    from a callback.

    A detector starts once. Call close(), or use it in a `with` block, when done.
    """

    def __init__(self, sources, model, *, every_nth_frame=None, post=None, loop_files=None,
                 reconnect_initial_ms=None, reconnect_max_ms=None, stall_timeout_ms=None, on_frame=None,
                 on_event=None):
        self._handle = None
        self._closing = False
        self._users = 0
        self._cond = threading.Condition()
        self.on_frame = on_frame
        self.on_event = on_event
        self.callback_errors = 0
        if isinstance(sources, (str, bytes, os.PathLike)):
            sources = [sources]
        encoded = [os.fsencode(s) for s in sources]
        cfg = _CConfig()
        _config_init(ctypes.byref(cfg))
        array = (ctypes.c_char_p * max(len(encoded), 1))(*encoded)
        cfg.sources = array
        cfg.source_count = len(encoded)
        cfg.model = os.fsencode(model)
        if post is not None:
            cfg.post = post.encode()
        if loop_files is not None:
            cfg.loop_files = 1 if loop_files else 0
        for name, value in (("every_nth_frame", every_nth_frame), ("reconnect_initial_ms", reconnect_initial_ms),
                            ("reconnect_max_ms", reconnect_max_ms), ("stall_timeout_ms", stall_timeout_ms)):
            if value is not None:
                setattr(cfg, name, int(value))
        err = ctypes.create_string_buffer(_ERROR_SIZE)
        handle = _open(ctypes.byref(cfg), err, _ERROR_SIZE)
        if not handle:
            raise Error(err.value.decode(errors="replace"))
        self._handle = handle
        # The C callbacks are set once and look the Python ones up on every call. They must live as long
        # as the handle: the library keeps the pointers.
        self._c_frame = _FrameFn(self._deliver_frame)
        self._c_event = _EventFn(self._deliver_event)
        _on_frame(handle, self._c_frame, None)
        _on_event(handle, self._c_event, None)

    # -- callbacks, on the library's threads --

    def _failed(self):
        self.callback_errors += 1
        traceback.print_exc()

    def _deliver_frame(self, pointer, _user):
        callback = self.on_frame
        if callback is None:
            return
        _in_callback.detector = self
        try:
            f = pointer.contents
            detections = []
            if f.count > 0:
                detections = [Detection(d.x, d.y, d.w, d.h, d.score, d.cls) for d in f.detections[:f.count]]
            callback(Frame(f.channel, f.seq, f.pts_us, f.capture_ns, f.width, f.height, detections))
        except BaseException:  # nothing may go back into the library's thread
            self._failed()
        finally:
            _in_callback.detector = None

    def _deliver_event(self, pointer, _user):
        callback = self.on_event
        if callback is None:
            return
        _in_callback.detector = self
        try:
            e = pointer.contents
            callback(Event(EventKind(e.kind), e.channel, (e.message or b"").decode(errors="replace")))
        except BaseException:
            self._failed()
        finally:
            _in_callback.detector = None

    # -- control --
    # A call that uses the handle counts itself in _users; close() frees the handle only when none is
    # left. No lock is held while the library runs: a callback may call stats() while another thread is
    # inside stop(), which waits for that callback.

    def _enter(self):
        with self._cond:
            if not self._handle:
                raise Error("the detector is closed")
            self._users += 1
            return self._handle

    def _leave(self):
        with self._cond:
            self._users -= 1
            self._cond.notify_all()

    def _not_from_callback(self, what):
        if getattr(_in_callback, "detector", None) is self:
            raise Error(f"{what}() cannot be called from this detector's callback")

    def start(self):
        handle = self._enter()
        try:
            err = ctypes.create_string_buffer(_ERROR_SIZE)
            with _live_lock:
                _live.add(self)
            if not _start(handle, err, _ERROR_SIZE):
                with _live_lock:
                    _live.discard(self)
                raise Error(err.value.decode(errors="replace"))
        finally:
            self._leave()

    def stop(self):
        """Returns when every thread has ended. Frames already sampled are delivered first."""
        self._not_from_callback("stop")
        try:
            handle = self._enter()
        except Error:
            return  # closed: already stopped
        try:
            _stop(handle)
        finally:
            self._leave()
        with _live_lock:
            _live.discard(self)

    def close(self):
        """Stops if running and frees the model. Calling it again does nothing."""
        self._not_from_callback("close")
        with self._cond:
            if not self._handle or self._closing:
                return
            self._closing = True
            handle = self._handle
        _stop(handle)  # the callbacks end here; until then they can still use the handle
        with self._cond:
            self._handle = None
            while self._users:
                self._cond.wait()
        _close(handle)
        with _live_lock:
            _live.discard(self)

    def wait(self, timeout=None):
        """Waits until every source has ended (files, without loop_files) or the detector is stopped or
        closed. False if `timeout` seconds passed first. It waits in short steps, so Ctrl-C interrupts
        it."""
        self._not_from_callback("wait")
        until = None if timeout is None else time.monotonic() + timeout
        while True:
            try:
                handle = self._enter()
            except Error:
                return True
            try:
                step = 100 if until is None else int(max(0.0, min(0.1, until - time.monotonic())) * 1000)
                if _wait(handle, step):
                    return True
            finally:
                self._leave()
            if until is not None and time.monotonic() >= until:
                return False

    def stats(self):
        """One ChannelStats per channel."""
        handle = self._enter()
        try:
            n = _channel_count(handle)
            if n <= 0:
                return []
            out = (_CStats * n)()
            n = _stats(handle, out, n)
            return [ChannelStats(s.decoded, s.sampled, s.inferred, s.dropped, s.errors, s.decode_errors,
                                 s.reconnects, bool(s.connected), s.cached_buffers) for s in out[:n]]
        finally:
            self._leave()

    @property
    def model_size(self):
        """(width, height) of the model's input."""
        handle = self._enter()
        try:
            return _model_width(handle), _model_height(handle)
        finally:
            self._leave()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def __del__(self):
        try:
            if self._handle:
                self.close()
        except Exception:  # the interpreter may be shutting down
            pass
