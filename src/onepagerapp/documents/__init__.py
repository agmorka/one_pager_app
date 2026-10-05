"""One Pager document module.

Owns reading and writing One Pager YAML documents from the configured base path
(local folder in development, the registry volume through the Files API in the
workspace). Separated from ``data_access`` which handles tabular/Delta data.
"""

from onepagerapp.documents.serialization import change_log_item
from onepagerapp.documents.store import OnePagerDocumentStore

__all__ = ["OnePagerDocumentStore", "change_log_item"]
