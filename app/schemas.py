from pydantic import BaseModel, EmailStr, Field


class User(BaseModel):
    email: str
    senha: str = Field(min_length=4)


class Message(BaseModel):
    text: str = Field(min_length=1)


class ParsedTransaction(BaseModel):
    tipo: str = "despesa"
    categoria: str = "Outros"
    subcategoria: str | None = None
    valor: float = 0


class PendingTransaction(BaseModel):
    tipo: str = "despesa"
    categoria: str = "Outros"
    subcategoria: str | None = None
    valor: float


class AdminResetRequest(BaseModel):
    email: str = Field(min_length=3)
