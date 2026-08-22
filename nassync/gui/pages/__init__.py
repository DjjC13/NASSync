"""The wizard pages, in the order the operator moves through them."""

from .conflicts import ConflictsPage
from .connect import ConnectPage
from .execute import ExecutePage
from .preview import PreviewPage
from .scan import ScanPage
from .summary import SummaryPage

__all__ = [
    "ConnectPage",
    "ScanPage",
    "PreviewPage",
    "ConflictsPage",
    "ExecutePage",
    "SummaryPage",
]
