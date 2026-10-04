"""People and form input: the signed-in user, Owner/SMEs, create input and results."""

from dataclasses import dataclass, field


@dataclass
class PersonRef:
    """A person referenced in a One Pager (Data Product Owner or SME).

    Mirrors the ``dataProductOwner`` / ``smes`` item shape of the JSON Schema.
    ``initials`` is the authorization key; ``email`` is for display only.
    """

    name: str
    initials: str
    email: str
    team: str | None = None


@dataclass
class CurrentUser:
    """The authenticated user of the current session.

    Attributes:
        username: Raw identity from Databricks (e.g. "MJOADM@BECOC001.onmicrosoft.com").
        initials: Corporate initials derived from the username (authorization key).
        display_name: Human-readable name used in change log / YAML ``createdBy``.
        email: Primary email from the workspace directory, or "" when unknown.
            For pre-filling forms only; never used for authorization.

    """

    username: str
    initials: str
    display_name: str
    email: str = ""


@dataclass
class NewOnePagerInput:
    """User input for creating a new One Pager (Editor, create mode)."""

    data_product: str
    product_name: str
    business_domain: str
    data_product_type: str
    description: str
    owner: PersonRef
    smes: list[PersonRef] = field(default_factory=list)
    business_problem_statement: str = ""


@dataclass
class ValidationError:
    """A single validation problem, reported (not raised) per Backend_Design §4.

    Attributes:
        field_path: Dotted path of the offending field (e.g. "dataProductOwner.email",
            "smes[1].initials"); "" for form-level errors.
        message: User-facing message.

    """

    field_path: str
    message: str


@dataclass
class CreateResult:
    """Outcome of a create attempt.

    Either ``one_pager_id`` is set (success) or ``errors`` is non-empty
    (validation failed, nothing was written).
    """

    one_pager_id: str | None = None
    version: str | None = None
    errors: list[ValidationError] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """Whether the One Pager was created."""
        return self.one_pager_id is not None and not self.errors
