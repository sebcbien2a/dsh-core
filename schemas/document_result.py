from pydantic import BaseModel, Field
from typing import Optional, Dict, Any, List

class DocumentResult(BaseModel):
    source_name: str
    profile_type: str
    extraction_tool: str
    raw_text: str
    business_type: str = "unknown"
    confidence: Optional[float] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    processing_notes: List[str] = Field(default_factory=list)