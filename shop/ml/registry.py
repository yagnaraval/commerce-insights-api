"""Process-wide model cache: the artifact is deserialised once, not per request."""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from pathlib import Path

import joblib
from django.conf import settings

logger = logging.getLogger(__name__)
MODEL_FILE = "return_model.joblib"
METRICS_FILE = "metrics.json"


@dataclass(frozen=True)
class LoadedModel:
    estimator: object
    version: str
    features: list[str]


def model_path() -> Path:
    return Path(settings.MODEL_DIR) / MODEL_FILE


class ModelRegistry:
    """Lazy, thread-safe cache keyed on the artifact's (path, mtime, size).

    A ``stat`` call per request is far cheaper than ``joblib.load`` and means a retrain in
    another process is picked up without restarting the server.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._stamp: tuple | None = None
        self._model: LoadedModel | None = None

    def get(self) -> LoadedModel | None:
        path = model_path()
        try:
            stat = path.stat()
        except FileNotFoundError:
            return None
        stamp = (str(path), stat.st_mtime_ns, stat.st_size)
        if stamp == self._stamp and self._model is not None:
            return self._model
        with self._lock:
            if stamp != self._stamp or self._model is None:
                bundle = joblib.load(path)
                self._model = LoadedModel(bundle["model"], bundle["version"], bundle["features"])
                self._stamp = stamp
                logger.info("loaded return model version=%s", self._model.version)
            return self._model

    def reset(self) -> None:
        with self._lock:
            self._stamp = None
            self._model = None


registry = ModelRegistry()
