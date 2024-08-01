from collections.abc import Generator
from typing import Final, Literal

from .fix_imports import FixImports

MAPPING: Final[dict[str, list[tuple[Literal["urllib.request", "urllib.parse", "urllib.error"], list[str]]]]]

def build_pattern() -> Generator[str, None, None]: ...

class FixUrllib(FixImports):
    def build_pattern(self): ...
    def transform_import(self, node, results) -> None: ...
    def transform_member(self, node, results): ...
    def transform_dot(self, node, results) -> None: ...
    def transform(self, node, results) -> None: ...
