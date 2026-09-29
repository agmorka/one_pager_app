"""One Pager document module.

Owns reading and writing One Pager YAML documents from the configured base path
(local folder in development, mounted volume in the workspace). Separated from
``data_access`` which handles tabular/Delta data.
"""

from onepagerapp.documents.store import OnePagerDocumentStore

__all__ = ["OnePagerDocumentStore"]
