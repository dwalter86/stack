from datetime import datetime
from pydantic import BaseModel, EmailStr, Field
from typing import Optional, List, Literal

class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"

class LoginRequest(BaseModel):
    email: EmailStr
    password: str

class Preferences(BaseModel):
    accounts_label: str = "Home"
    sections_label: str = "Sections"
    items_label: str = "Items"
    show_slugs: bool = False

class PreferencesUpdate(BaseModel):
    accounts_label: Optional[str] = None
    sections_label: Optional[str] = None
    items_label: Optional[str] = None
    show_slugs: Optional[bool] = None

class MeOut(BaseModel):
    id: str
    email: EmailStr
    name: str
    user_type: str
    is_admin: bool
    preferences: Preferences = Preferences()

class AccountOut(BaseModel):
    id: str
    name: str

class AccountCreate(BaseModel):
    name: str

class AccountUpdate(BaseModel):
    name: str

class ItemCreate(BaseModel):
    name: str
    data: dict = Field(default_factory=dict)

class ItemUpdate(BaseModel):
    name: Optional[str] = None
    data: Optional[dict] = None

class ItemOut(BaseModel):
    id: str
    name: str
    data: dict
    created_at: datetime
    comment_count: int = 0

class ItemsPage(BaseModel):
    items: List[ItemOut]
    next: Optional[str]

class AdminUser(BaseModel):
    id: str
    email: EmailStr
    name: str
    user_type: str
    is_active: bool
    preferences: Optional[Preferences] = None

class CreateAdmin(BaseModel):
    email: EmailStr
    password: str
    name: str
    user_type: Literal["super_admin", "admin", "standard"] = "admin"
    accounts: List[str] = Field(default_factory=list)

class AdminUserUpdate(BaseModel):
    name: Optional[str] = None
    user_type: Optional[str] = None
    is_active: Optional[bool] = None
    accounts: Optional[list[str]] = None
    
class SectionBase(BaseModel):
    slug: str
    label: str
    schema: dict = Field(default_factory=dict)

class SectionCreate(SectionBase):
    pass

class SectionUpdate(BaseModel):
    label: str
    schema: dict = Field(default_factory=dict)

class SectionOut(SectionBase):
    id: str

class CommentCreate(BaseModel):
    comment: str
    user_name: Optional[str] = None

class CommentOut(BaseModel):
    id: str
    item_id: str
    user_name: Optional[str] = None
    comment: str
    created_at: datetime
