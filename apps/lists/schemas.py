from typing import Literal

from pydantic import BaseModel


class InsightSchema(BaseModel):
    type: str
    title: str
    message: str
    severity: Literal[
        'info',
        'warning',
        'critical',
    ]

class InsightResponseSchema(BaseModel):
    insights: list[InsightSchema]