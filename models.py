from pydantic import BaseModel
from typing import Optional, Dict, Any
from datetime import datetime


class TemplateCreate(BaseModel):
    name: str
    description: Optional[str] = None
    total_questions: int
    answer_key: Dict[str, Any]


class TemplateResponse(BaseModel):
    id: int
    name: str
    description: Optional[str]
    total_questions: int
    answer_key: Dict[str, Any]
    created_at: datetime
    
    class Config:
        from_attributes = True


class OMRSheetUpload(BaseModel):
    template_id: int


class OMRSheetResponse(BaseModel):
    id: int
    template_id: int
    student_name: Optional[str]
    roll_number: Optional[str]
    exam_date: Optional[str]
    other_details: Optional[Dict[str, Any]]
    responses: Dict[str, Any]
    image_path: Optional[str]
    correct_count: int
    wrong_count: int
    total_questions: int
    percentage: str
    created_at: datetime
    
    class Config:
        from_attributes = True


class AnswerKeyCreate(BaseModel):
    name: str
    description: Optional[str] = None
    answer_key: Dict[str, Any]


class AnswerKeyResponse(BaseModel):
    id: int
    name: str
    description: Optional[str]
    answer_key: Dict[str, Any]
    created_at: datetime
    
    class Config:
        from_attributes = True

