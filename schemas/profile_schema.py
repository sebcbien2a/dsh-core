from pydantic import BaseModel

class DocumentProfile(BaseModel):
    extension: str
    has_text_layer: bool
    word_count: int
    has_tables: bool
    has_images: bool
    is_scanned: bool
    profile_type: str = "unknown"