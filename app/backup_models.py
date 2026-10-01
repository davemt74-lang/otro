from __future__ import annotations

from typing import Any
from pydantic import BaseModel, Field


class BackupPolicyUpdate(BaseModel):
    include_app_data: bool | None = None
    retain_manual: int | None = Field(default=None, ge=1, le=100)
    retain_automatic: int | None = Field(default=None, ge=1, le=100)
    retain_pre_restore: int | None = Field(default=None, ge=1, le=20)
