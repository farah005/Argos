"""Tests of the image diagnostics of the camtest tool."""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from argos_edge.camtest import frame_quality as _fq, frozen  # noqa: E402
from argos_edge.main import load_config  # noqa: E402

_CT = load_config(str(Path(__file__).resolve().parents[2] / "config" / "edge.yaml"))["camtest"]


def frame_quality(frame):
    return _fq(frame, _CT["dark_mean"], _CT["flat_std"])


def _scene(seed=0):
    rng = np.random.default_rng(seed)
    return rng.integers(30, 220, (120, 160, 3), dtype=np.uint8)


def test_normal_image_is_usable():
    ok, why = frame_quality(_scene())
    assert ok and "luminosité" in why


def test_black_image_rejected():
    ok, why = frame_quality(np.zeros((120, 160, 3), np.uint8))
    assert not ok and "noire" in why


def test_flat_grey_image_rejected():
    ok, why = frame_quality(np.full((120, 160, 3), 128, np.uint8))
    assert not ok and "uniforme" in why


def test_missing_image_rejected():
    assert frame_quality(None)[0] is False


def test_frozen_stream():
    a = _scene(1)
    assert frozen([a, a.copy(), a.copy()])
    assert not frozen([a, _scene(2)])
    assert not frozen([a])
