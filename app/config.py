from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    database_url defaults to a local SQLite file so the project runs with
    zero external services. docker-compose overrides this with a Postgres
    URL for a closer-to-production setup. Tests override it again to a
    disposable SQLite file (see tests/conftest.py) so the suite never
    touches your dev data.

    The two access codes, the confidential ID number and the hint flag drive
    Knowledge Search's permissions (see app/access.py). Opening a Confidential
    document takes the confidential code *and* the ID number - a 5-digit
    number, typed on the page's 5-dot pad. The defaults are demo values so the
    project works the moment it is cloned - before putting it on a public
    URL, set TEAM_ACCESS_CODE, CONFIDENTIAL_ACCESS_CODE and
    CONFIDENTIAL_ID_NUMBER to your own and SHOW_ACCESS_HINT=false (the hint box
    prints the codes and the ID number on the page).

    The max_* numbers cap what one upload may cost (see app/limits.py). The
    defaults let a 25 MB file through - about 200,000 rows of an ordinary
    dozen-column export, or up to a million short ones - and keep what that
    costs in check. Measured on top of an idle server (without the search model
    loaded): one such file adds about 110 MB while it is checked, a file plus
    three related files about 170 MB, two of those at the same moment about
    320 MB, and two of the heaviest shape (a million short rows that all have
    problems) about 380 MB - the most a visitor can make the server hold at
    once. That suits a laptop or a host with a gigabyte or two to spare. On a
    512 MB host lower them (see the README, "Try a sample, and upload limits":
    MAX_UPLOAD_MB=5, MAX_CHECK_ROWS=30000, MAX_REFERENCE_FILES=1,
    MAX_CONCURRENT_UPLOADS=1).
    """

    database_url: str = "sqlite:///./dqp.db"

    team_access_code: str = "280010"
    confidential_access_code: str = "938032"
    confidential_id_number: str = "69712"  # exactly 5 digits: the page's pad has 5 dots
    show_access_hint: bool = True

    max_upload_mb: int = Field(default=25, gt=0)  # biggest single file on any upload form
    max_check_rows: int = Field(default=1_000_000, gt=0)  # rows one data-quality check reads
    max_check_columns: int = Field(default=100, gt=0)
    max_check_cells: int = Field(default=6_000_000, gt=0)  # rows x columns: what the checks' work really scales with
    max_reference_files: int = Field(default=3, gt=0)  # related files one run may bring along
    max_incident_rows: int = Field(default=25_000, gt=0)  # rows in one logs / metrics upload
    max_document_chunks: int = Field(default=1_000, gt=0)  # ~120,000 words; each chunk is embedded at upload
    max_concurrent_uploads: int = Field(default=2, gt=0)  # big uploads worked on at once; the rest wait their turn

    @field_validator("confidential_id_number", mode="before")
    @classmethod
    def _id_number_is_five_digits(cls, value):
        # Refuse to start rather than quietly lock everyone out of Confidential:
        # the pad has five dots, so an ID of any other length could never be typed.
        text = value.strip() if isinstance(value, str) else value
        if not (isinstance(text, str) and len(text) == 5 and text.isascii() and text.isdigit()):
            raise ValueError("CONFIDENTIAL_ID_NUMBER must be exactly 5 digits (the page's ID pad has 5 dots), e.g. 69712")
        return text

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    model_config = SettingsConfigDict(env_file=".env", env_prefix="")


settings = Settings()
