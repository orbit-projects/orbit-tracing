# Copyright 2026-present Orbit Contributors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Validated resource and batching limits for the OpenTelemetry tracer adapter."""

from __future__ import annotations

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    StrictStr,
    field_validator,
    model_validator,
)


class OpenTelemetryConfig(BaseModel):
    """Bounded SDK configuration owned by one Orbit application process."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True, validate_default=True)

    service_name: StrictStr = Field(min_length=1, max_length=255)
    max_queue_size: StrictInt = Field(default=2_048, ge=1, le=100_000)
    schedule_delay_millis: StrictInt = Field(default=5_000, ge=1, le=300_000)
    export_timeout_millis: StrictInt = Field(default=30_000, ge=1, le=300_000)
    max_export_batch_size: StrictInt = Field(default=512, ge=1, le=10_000)

    @field_validator("service_name")
    @classmethod
    def validate_service_name(cls, value: str) -> str:
        """Reject whitespace-only and control-character service identities."""
        if not value.strip() or any(
            ord(character) < 32 or ord(character) == 127 for character in value
        ):
            raise ValueError("service_name must be non-empty printable text.")
        return value

    @model_validator(mode="after")
    def validate_batch_size(self) -> OpenTelemetryConfig:
        """Ensure the exporter batch cannot exceed the queue that retains spans."""
        if self.max_export_batch_size > self.max_queue_size:
            raise ValueError("max_export_batch_size cannot exceed max_queue_size.")
        return self
