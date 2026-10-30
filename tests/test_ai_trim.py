"""Supported-part trimming must never turn a wrong clinical claim into a misleading one.
Drops only pure annotations (dates, bare numbers/units/side) that the quotes don't support; any value with a
status/change qualifier, or whose unsupported part carries meaning, is rejected as a whole."""
import os
from pathlib import Path

os.environ.setdefault("DATA_DIR", str(Path(__file__).parent / "fixtures"))   # same as test_backend.py
os.environ.setdefault("MOCK", "1")

from backend.ai import _supported_part  # noqa: E402

Q_ASS = ["Thrombo ASS 100mg oral 1 0 0 0 ST"]
Q_BLOP = ["Blopress 16mg oral 1 0 1 0 ST"]
Q_CAUSE = ["In Zusammenschau der Befund wurde die Schwindel und Augensymptomatik auf eine artherosklerotische "
           "Karotisstenose zurückgeführt"]
Q_STEN = ["Maximal 60%ige Abgangsstenose der Arteria carotis interna links."]
Q_W = ["Gewicht: 78 kg", "Gewicht: 67 kg"]


def keep(value, quotes, derived=False):
    return _supported_part(value, derived, quotes)


def test_supported_values_unchanged():
    assert keep("Thrombo ASS 100 mg 1-0-0", Q_ASS) == "Thrombo ASS 100 mg 1-0-0"
    assert keep("Quadrantectomy + IORT on 16.05.2016", ["Quadrantenresektion + IORT am 16.05.2016"]) \
        == "Quadrantectomy + IORT on 16.05.2016"


def test_pure_annotations_are_dropped():
    assert keep("78 kg (2015-06-28), 67 kg (2016-04-05)", Q_W) == "78 kg, 67 kg"
    assert keep("Hypertensive crisis; cause: arteriosclerotic carotid stenosis (60% left)", Q_CAUSE) \
        == "Hypertensive crisis; cause: arteriosclerotic carotid stenosis"
    assert keep("Previous episode in 06/2015; cause: arteriosclerotic carotid stenosis", Q_CAUSE) \
        == "Previous episode; cause: arteriosclerotic carotid stenosis"


def test_wrong_dose_rejected():
    assert keep("Blopress 32 mg 1-0-1", Q_BLOP) is None
    assert keep("Blopress 16 mg 1-0-1 (32 mg)", Q_BLOP) is None          # conflicting dose, not an annotation
    assert keep("Blopress 16 mg 1-0-1; Blopress 32 mg 1-0-1", Q_BLOP) is None


def test_status_or_change_qualifiers_never_trimmed():
    assert keep("Thrombo ASS 100 mg 1-0-0 (paused 10.05.2016)", Q_ASS) is None
    assert keep("Thrombo ASS 100 mg 1-0-0 until 05/2016", Q_ASS) is None
    assert keep("Thrombo ASS 100 mg 1-0-0; stopped 2016", Q_ASS) is None
    assert keep("Blopress 16 mg 1-0-1 (increased to 32 mg)", Q_BLOP) is None
    assert keep("Blopress 16 mg 1-0-1; dose reduced to 8 mg", Q_BLOP) is None


def test_wrong_side_rejected():
    assert keep("60% stenosis of the right internal carotid artery", Q_STEN) is None
    assert keep("Carotid stenosis 60% (right)", Q_STEN) is None
    assert keep("Carotid stenosis 60% left; right ICA 80%", Q_STEN) is None
