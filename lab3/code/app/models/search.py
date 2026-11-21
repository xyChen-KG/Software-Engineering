"""共享账目的查询范围。"""

from datetime import date
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, Field, model_validator

from app.models import EntryStatus, EntryType
from app.models.validation import MAX_INTEGER


class SearchFilters(BaseModel):
    """金额以分计，日期和金额边界均包含在内。"""

    kind: EntryType | None = None
    status: EntryStatus | None = None
    start: date | None = None
    end: date | None = None
    category: str = Field(default="", max_length=60)
    member_id: UUID | None = None
    minimum: int | None = Field(default=None, ge=0, le=MAX_INTEGER)
    maximum: int | None = Field(default=None, ge=0, le=MAX_INTEGER)
    keyword: str = Field(default="", max_length=200)
    offset: int = Field(default=0, ge=0, le=MAX_INTEGER)
    limit: int = Field(default=30, ge=1, le=100)

    @model_validator(mode="after")
    def ordered(self):
        """拒绝反向区间。"""
        if self.start and self.end and self.start > self.end:
            raise ValueError("开始日期晚于结束日期")
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise ValueError("最低金额高于最高金额")
        return self


class LogFilters(BaseModel):
    """日志按带时区的起止时刻、经办成员、业务对象和动作筛选。"""

    actor_member_id: UUID | None = None
    action: str | None = Field(default=None, max_length=100)
    object_id: UUID | None = None
    start: AwareDatetime | None = None
    end: AwareDatetime | None = None
    limit: int = Field(default=100, ge=1, le=1000)
    offset: int = Field(default=0, ge=0, le=MAX_INTEGER)

    @model_validator(mode="after")
    def ordered(self):
        """时间区间两端均包含。"""
        if self.start and self.end and self.start > self.end:
            raise ValueError("开始时间晚于结束时间")
        return self
