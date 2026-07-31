# app/core/capture.py
from __future__ import annotations
from threading import Lock
import mss
import numpy as np
import cv2


class ScreenCapture:
    """Thread-safe mss singleton. One instance for the entire app lifetime."""

    _instance: ScreenCapture | None = None
    _init_lock = Lock()

    @classmethod
    def get(cls) -> ScreenCapture:
        if cls._instance is None:
            with cls._init_lock:
                if cls._instance is None:
                    obj = cls.__new__(cls)
                    obj._sct = mss.mss()
                    obj._mutex = Lock()
                    cls._instance = obj
        return cls._instance

    def grab(self, region: dict) -> np.ndarray:
        """Capture region and return BGR numpy array."""
        with self._mutex:
            raw = self._sct.grab(region)
        img = np.array(raw)
        return cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
