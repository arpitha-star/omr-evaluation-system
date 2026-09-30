from fastapi import FastAPI, UploadFile, File, Depends, HTTPException, Query, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
from typing import List, Optional
import os
import shutil
import json
import logging
import traceback
from pathlib import Path
from io import BytesIO
from PIL import Image
from datetime import datetime

# Setup logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

from database import get_db, init_db, Template, OMRSheet, AnswerKey
from models import (
    TemplateCreate, TemplateResponse,
    OMRSheetUpload, OMRSheetResponse,
    AnswerKeyCreate, AnswerKeyResponse
)
from gemini_service import (
    process_omr_sheet,
    create_answer_key_from_image,
    extract_name_from_image,
    process_omr_from_image_data,
)

app = FastAPI(title="OMR Sheet Processor API")

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Vite default port
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Create uploads directory
UPLOAD_DIR = Path("uploads")
UPLOAD_DIR.mkdir(exist_ok=True)

# Serve static files from uploads directory
app.mount("/uploads", StaticFiles(directory=str(UPLOAD_DIR)), name="uploads")

# Initialize database
init_db()


@app.get("/")
def read_root():
    return {"message": "OMR Sheet Processor API"}


# Template endpoints
@app.post("/api/templates", response_model=TemplateResponse)
def create_template(template: TemplateCreate, db: Session = Depends(get_db)):
    db_template = Template(
        name=template.name,
        description=template.description,
        total_questions=template.total_questions,
        answer_key=json.dumps(template.answer_key)
    )
    db.add(db_template)
    db.commit()
    db.refresh(db_template)
    
    db_template.answer_key = json.loads(db_template.answer_key)
    return db_template


@app.get("/api/templates", response_model=List[TemplateResponse])
def get_templates(db: Session = Depends(get_db)):
    templates = db.query(Template).all()
    for template in templates:
        template.answer_key = json.loads(template.answer_key)
    return templates


@app.get("/api/templates/{template_id}", response_model=TemplateResponse)
def get_template(template_id: int, db: Session = Depends(get_db)):
    template = db.query(Template).filter(Template.id == template_id).first()
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")
    template.answer_key = json.loads(template.answer_key)
    return template


@app.delete("/api/templates/{template_id}")
def delete_template(template_id: int, db: Session = Depends(get_db)):
    template = db.query(Template).filter(Template.id == template_id).first()
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")
    db.delete(template)
    db.commit()
    return {"message": "Template deleted successfully"}


# OMR Sheet endpoints
@app.post("/api/omr-sheets/upload", response_model=OMRSheetResponse)
async def upload_omr_sheet(
    file: UploadFile = File(...),
    template_id: int = Query(...),
    db: Session = Depends(get_db)
):
    # Verify template exists
    template = db.query(Template).filter(Template.id == template_id).first()
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")
    
    # Save uploaded file
    file_path = UPLOAD_DIR / f"{file.filename}"
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
    
    try:
        # Process with Gemini
        template_answer_key = json.loads(template.answer_key)
        result = process_omr_sheet(str(file_path), template_answer_key)
        
        # Calculate statistics
        responses = result.get("responses", {})
        correct_count = 0
        wrong_count = 0
        unanswered_count = 0
        
        for question_num, student_answer in responses.items():
            correct_answer = template_answer_key.get(str(question_num))
            if not student_answer or student_answer.strip() == "":
                unanswered_count += 1
            elif student_answer.strip().upper() == str(correct_answer).strip().upper():
                correct_count += 1
            else:
                wrong_count += 1
        
        total_answered = correct_count + wrong_count
        total_questions = template.total_questions
        percentage = (correct_count / total_questions * 100) if total_questions > 0 else 0
        
        # Create OMR sheet record
        db_omr_sheet = OMRSheet(
            template_id=template_id,
            student_name=result.get("student_name"),
            roll_number=result.get("roll_number"),
            exam_date=result.get("exam_date"),
            other_details=json.dumps(result.get("other_details", {})),
            responses=json.dumps(result.get("responses", {})),
            image_path=str(file_path),
            correct_count=correct_count,
            wrong_count=wrong_count,
            total_questions=total_questions,
            percentage=f"{percentage:.2f}%"
        )
        db.add(db_omr_sheet)
        db.commit()
        db.refresh(db_omr_sheet)
        
        # Convert JSON strings back to dicts
        db_omr_sheet.responses = json.loads(db_omr_sheet.responses)
        db_omr_sheet.other_details = json.loads(db_omr_sheet.other_details) if db_omr_sheet.other_details else {}
        
        return db_omr_sheet
    
    except Exception as e:
        # Clean up file on error
        if file_path.exists():
            file_path.unlink()
        raise HTTPException(status_code=500, detail=f"Error processing OMR sheet: {str(e)}")


# New endpoint: upload using an existing Answer Key (no Template selection needed)
@app.post("/api/omr-sheets/upload-by-answer-key", response_model=OMRSheetResponse)
async def upload_omr_sheet_by_answer_key(
    file: UploadFile = File(...),
    answer_key_id: int = Query(...),
    db: Session = Depends(get_db)
):
    # Verify answer key exists
    answer_key = db.query(AnswerKey).filter(AnswerKey.id == answer_key_id).first()
    if not answer_key:
        raise HTTPException(status_code=404, detail="Answer key not found")

    # Ensure we have a Template with this answer key to satisfy existing schema
    try:
        ak_dict = json.loads(answer_key.answer_key)
    except Exception:
        ak_dict = {}
    total_questions = len(ak_dict.keys())

    # Create or reuse a template that mirrors the answer key
    template_name = f"AK #{answer_key.id}: {answer_key.name}"
    # Try to find existing template with same name and total_questions
    template = db.query(Template).filter(Template.name == template_name).first()
    if not template:
        template = Template(
            name=template_name,
            description=answer_key.description,
            total_questions=total_questions,
            answer_key=answer_key.answer_key,
        )
        db.add(template)
        db.commit()
        db.refresh(template)

    # Delegate to existing upload logic using the template
    class DummyUploadFile:
        def __init__(self, uf: UploadFile):
            self.filename = uf.filename
            self.file = uf.file

    # Reuse core of upload path
    file_path = UPLOAD_DIR / f"{file.filename}"
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    try:
        template_answer_key = json.loads(template.answer_key)
        result = process_omr_sheet(str(file_path), template_answer_key)

        responses = result.get("responses", {})
        correct_count = 0
        wrong_count = 0
        unanswered_count = 0

        for question_num, student_answer in responses.items():
            correct_answer = template_answer_key.get(str(question_num))
            if not student_answer or student_answer.strip() == "":
                unanswered_count += 1
            elif student_answer.strip().upper() == str(correct_answer).strip().upper():
                correct_count += 1
            else:
                wrong_count += 1

        total_answered = correct_count + wrong_count
        total_questions = template.total_questions
        percentage = (correct_count / total_questions * 100) if total_questions > 0 else 0

        db_omr_sheet = OMRSheet(
            template_id=template.id,
            student_name=result.get("student_name"),
            roll_number=result.get("roll_number"),
            exam_date=result.get("exam_date"),
            other_details=json.dumps(result.get("other_details", {})),
            responses=json.dumps(result.get("responses", {})),
            image_path=str(file_path),
            correct_count=correct_count,
            wrong_count=wrong_count,
            total_questions=total_questions,
            percentage=f"{percentage:.2f}%"
        )
        db.add(db_omr_sheet)
        db.commit()
        db.refresh(db_omr_sheet)

        db_omr_sheet.responses = json.loads(db_omr_sheet.responses)
        db_omr_sheet.other_details = json.loads(db_omr_sheet.other_details) if db_omr_sheet.other_details else {}

        return db_omr_sheet
    except Exception as e:
        if file_path.exists():
            file_path.unlink()
        raise HTTPException(status_code=500, detail=f"Error processing OMR sheet: {str(e)}")


# Helpers
def _crop_bytes(image_bytes: bytes, x: float, y: float, w: float, h: float) -> bytes:
    """
    Crop image using relative coords (0..1) and return bytes (PNG).
    """
    img = Image.open(BytesIO(image_bytes))
    width, height = img.size
    left = max(0, min(width, int(x * width)))
    top = max(0, min(height, int(y * height)))
    right = max(0, min(width, int((x + w) * width)))
    bottom = max(0, min(height, int((y + h) * height)))
    cropped = img.crop((left, top, right, bottom))
    buf = BytesIO()
    cropped.save(buf, format='PNG')
    return buf.getvalue()

def _save_bytes(prefix: str, orig_filename: str, data: bytes) -> str:
    """Save bytes into uploads folder and return URL path for frontend."""
    ext = os.path.splitext(orig_filename)[1] or ".png"
    # Use PNG for cropped images to avoid format issues
    if prefix in ["name_crop", "omr_crop"]:
        ext = ".png"
    ts = datetime.utcnow().strftime("%Y%m%d%H%M%S%f")
    filename = f"{prefix}_{ts}{ext}"
    path = UPLOAD_DIR / filename
    with open(path, "wb") as f:
        f.write(data)
    # Return URL path instead of file path
    return f"/uploads/{filename}"


@app.post("/api/extract-name")
async def api_extract_name(
    file: UploadFile = File(...),
    x: float = Query(..., ge=0.0, le=1.0),
    y: float = Query(..., ge=0.0, le=1.0),
    w: float = Query(..., ge=0.0, le=1.0),
    h: float = Query(..., ge=0.0, le=1.0),
):
    image_bytes = await file.read()
    try:
        cropped = _crop_bytes(image_bytes, x, y, w, h)
        saved_path = _save_bytes("name_crop", file.filename, cropped)
        result = extract_name_from_image(cropped)
        result["image_path"] = saved_path
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error extracting name: {str(e)}")


@app.post("/api/extract-name-from-cropped")
async def api_extract_name_from_cropped(
    file: UploadFile = File(...),
):
    """Extract name from already cropped image - no coordinates needed, image is already cropped."""
    image_bytes = await file.read()
    try:
        saved_path = _save_bytes("name_crop", file.filename, image_bytes)
        result = extract_name_from_image(image_bytes)
        result["image_path"] = saved_path
        logger.info(f"Name extraction result: student_name={result.get('student_name')}, roll_number={result.get('roll_number')}, exam_date={result.get('exam_date')}")
        # Ensure we return None instead of empty string
        if result.get("student_name") == "":
            result["student_name"] = None
        if result.get("roll_number") == "":
            result["roll_number"] = None
        if result.get("exam_date") == "":
            result["exam_date"] = None
        # Ensure exam_date field exists
        if "exam_date" not in result:
            result["exam_date"] = None
        return result
    except Exception as e:
        logger.error(f"Error extracting name: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error extracting name: {str(e)}")


@app.post("/api/omr-sheets/process-cropped-omr-by-answer-key", response_model=OMRSheetResponse)
async def process_cropped_omr_by_answer_key(
    file: UploadFile = File(...),
    answer_key_id: int = Query(...),
    student_name: Optional[str] = Query(None),
    roll_number: Optional[str] = Query(None),
    exam_date: Optional[str] = Query(None),
    db: Session = Depends(get_db)
):
    """Process already cropped OMR image - no coordinates needed, image is already cropped."""
    logger.info(f"Received parameters - student_name: '{student_name}', roll_number: '{roll_number}', exam_date: '{exam_date}'")
    # Load answer key
    answer_key = db.query(AnswerKey).filter(AnswerKey.id == answer_key_id).first()
    if not answer_key:
        raise HTTPException(status_code=404, detail="Answer key not found")
    ak = json.loads(answer_key.answer_key)

    image_bytes = await file.read()
    cropped_path = _save_bytes("omr_crop", file.filename, image_bytes)

    # Process cropped region (image is already cropped)
    result = process_omr_from_image_data(image_bytes, ak)

    # Create or reuse template mirror
    total_questions = len(ak.keys())
    template_name = f"AK #{answer_key.id}: {answer_key.name}"
    template = db.query(Template).filter(Template.name == template_name).first()
    if not template:
        template = Template(
            name=template_name,
            description=answer_key.description,
            total_questions=total_questions,
            answer_key=answer_key.answer_key,
        )
        db.add(template)
        db.commit()
        db.refresh(template)

    # Score and persist sheet
    responses = result.get("responses", {})
    correct_count = 0
    wrong_count = 0
    for question_num, student_answer in responses.items():
        correct_answer = ak.get(str(question_num))
        if student_answer and str(student_answer).strip().upper() == str(correct_answer).strip().upper():
            correct_count += 1
        else:
            wrong_count += 1

    percentage = (correct_count / total_questions * 100) if total_questions > 0 else 0
    
    # Prioritize passed parameters over result, and handle empty strings
    final_student_name = None
    if student_name and isinstance(student_name, str) and student_name.strip():
        final_student_name = student_name.strip()
        logger.info(f"Using student_name from parameter: '{final_student_name}'")
    elif result.get("student_name") and isinstance(result.get("student_name"), str) and result.get("student_name").strip():
        final_student_name = result.get("student_name").strip()
        logger.info(f"Using student_name from OMR result: '{final_student_name}'")
    else:
        logger.warning(f"No valid student_name found (param: '{student_name}', result: '{result.get('student_name')}')")
    
    final_roll_number = None
    if roll_number and isinstance(roll_number, str) and roll_number.strip():
        final_roll_number = roll_number.strip()
        logger.info(f"Using roll_number from parameter: '{final_roll_number}'")
    elif result.get("roll_number") and isinstance(result.get("roll_number"), str) and result.get("roll_number").strip():
        final_roll_number = result.get("roll_number").strip()
        logger.info(f"Using roll_number from OMR result: '{final_roll_number}'")
    
    final_exam_date = None
    if exam_date and isinstance(exam_date, str) and exam_date.strip():
        final_exam_date = exam_date.strip()
        logger.info(f"Using exam_date from parameter: '{final_exam_date}'")
    elif result.get("exam_date") and isinstance(result.get("exam_date"), str) and result.get("exam_date").strip():
        final_exam_date = result.get("exam_date").strip()
        logger.info(f"Using exam_date from OMR result: '{final_exam_date}'")
    
    logger.info(f"Final values to save - student_name: '{final_student_name}', roll_number: '{final_roll_number}', exam_date: '{final_exam_date}'")
    
    db_omr_sheet = OMRSheet(
        template_id=template.id,
        student_name=final_student_name,
        roll_number=final_roll_number,
        exam_date=final_exam_date,
        other_details=json.dumps(result.get("other_details", {})),
        responses=json.dumps(responses),
        image_path=cropped_path,
        correct_count=correct_count,
        wrong_count=wrong_count,
        total_questions=total_questions,
        percentage=f"{percentage:.2f}%"
    )
    db.add(db_omr_sheet)
    db.commit()
    db.refresh(db_omr_sheet)

    db_omr_sheet.responses = json.loads(db_omr_sheet.responses)
    db_omr_sheet.other_details = json.loads(db_omr_sheet.other_details) if db_omr_sheet.other_details else {}
    
    # Log the final saved name for debugging
    logger.info(f"OMR sheet saved with ID {db_omr_sheet.id}, student_name: '{db_omr_sheet.student_name}'")
    
    return db_omr_sheet


@app.post("/api/omr-sheets/process-cropped-by-answer-key", response_model=OMRSheetResponse)
async def process_cropped_by_answer_key(
    file: UploadFile = File(...),
    answer_key_id: int = Query(...),
    x: float = Query(..., ge=0.0, le=1.0),
    y: float = Query(..., ge=0.0, le=1.0),
    w: float = Query(..., ge=0.0, le=1.0),
    h: float = Query(..., ge=0.0, le=1.0),
    db: Session = Depends(get_db)
):
    # Load answer key
    answer_key = db.query(AnswerKey).filter(AnswerKey.id == answer_key_id).first()
    if not answer_key:
        raise HTTPException(status_code=404, detail="Answer key not found")
    ak = json.loads(answer_key.answer_key)

    image_bytes = await file.read()
    cropped = _crop_bytes(image_bytes, x, y, w, h)
    cropped_path = _save_bytes("omr_crop", file.filename, cropped)

    # Process cropped region
    result = process_omr_from_image_data(cropped, ak)

    # Create or reuse template mirror
    total_questions = len(ak.keys())
    template_name = f"AK #{answer_key.id}: {answer_key.name}"
    template = db.query(Template).filter(Template.name == template_name).first()
    if not template:
        template = Template(
            name=template_name,
            description=answer_key.description,
            total_questions=total_questions,
            answer_key=answer_key.answer_key,
        )
        db.add(template)
        db.commit()
        db.refresh(template)

    # Score and persist sheet (no image path for cropped processing)
    responses = result.get("responses", {})
    correct_count = 0
    wrong_count = 0
    for question_num, student_answer in responses.items():
        correct_answer = ak.get(str(question_num))
        if student_answer and str(student_answer).strip().upper() == str(correct_answer).strip().upper():
            correct_count += 1
        else:
            wrong_count += 1

    percentage = (correct_count / total_questions * 100) if total_questions > 0 else 0
    db_omr_sheet = OMRSheet(
        template_id=template.id,
        student_name=result.get("student_name"),
        roll_number=result.get("roll_number"),
        exam_date=result.get("exam_date"),
        other_details=json.dumps(result.get("other_details", {})),
        responses=json.dumps(responses),
        image_path=cropped_path,
        correct_count=correct_count,
        wrong_count=wrong_count,
        total_questions=total_questions,
        percentage=f"{percentage:.2f}%"
    )
    db.add(db_omr_sheet)
    db.commit()
    db.refresh(db_omr_sheet)

    db_omr_sheet.responses = json.loads(db_omr_sheet.responses)
    db_omr_sheet.other_details = json.loads(db_omr_sheet.other_details) if db_omr_sheet.other_details else {}
    return db_omr_sheet


@app.get("/api/omr-sheets", response_model=List[OMRSheetResponse])
def get_omr_sheets(db: Session = Depends(get_db)):
    omr_sheets = db.query(OMRSheet).all()
    for sheet in omr_sheets:
        sheet.responses = json.loads(sheet.responses)
        sheet.other_details = json.loads(sheet.other_details) if sheet.other_details else {}
        # Ensure default values for backward compatibility
        if sheet.correct_count is None:
            sheet.correct_count = 0
        if sheet.wrong_count is None:
            sheet.wrong_count = 0
        if sheet.total_questions is None:
            sheet.total_questions = 0
        if not sheet.percentage:
            sheet.percentage = "0%"
        # Ensure student_name is properly returned (handle empty strings)
        if sheet.student_name == "":
            sheet.student_name = None
    return omr_sheets


@app.get("/api/omr-sheets/{sheet_id}", response_model=OMRSheetResponse)
def get_omr_sheet(sheet_id: int, db: Session = Depends(get_db)):
    sheet = db.query(OMRSheet).filter(OMRSheet.id == sheet_id).first()
    if not sheet:
        raise HTTPException(status_code=404, detail="OMR sheet not found")
    sheet.responses = json.loads(sheet.responses)
    sheet.other_details = json.loads(sheet.other_details) if sheet.other_details else {}
    # Ensure default values for backward compatibility
    if sheet.correct_count is None:
        sheet.correct_count = 0
    if sheet.wrong_count is None:
        sheet.wrong_count = 0
    if sheet.total_questions is None:
        sheet.total_questions = 0
    if not sheet.percentage:
        sheet.percentage = "0%"
    # Ensure student_name is properly returned (handle empty strings)
    if sheet.student_name == "":
        sheet.student_name = None
    return sheet


@app.delete("/api/omr-sheets/{sheet_id}")
def delete_omr_sheet(sheet_id: int, db: Session = Depends(get_db)):
    sheet = db.query(OMRSheet).filter(OMRSheet.id == sheet_id).first()
    if not sheet:
        raise HTTPException(status_code=404, detail="OMR sheet not found")
    
    # Delete image file if exists
    if sheet.image_path and os.path.exists(sheet.image_path):
        os.remove(sheet.image_path)
    
    db.delete(sheet)
    db.commit()
    return {"message": "OMR sheet deleted successfully"}


# Answer Key endpoints
@app.post("/api/answer-keys/create", response_model=AnswerKeyResponse)
async def create_answer_key(
    name: str = Form(...),
    description: Optional[str] = Form(None),
    file: UploadFile = File(...),
    db: Session = Depends(get_db)
):
    try:
        logger.info(f"Starting answer key creation - Name: {name}, Description: {description}")
        logger.info(f"File received - filename: {file.filename}, content_type: {file.content_type}")
        
        # Read raw image data directly from uploaded file
        image_data = await file.read()
        logger.info(f"Image data read - size: {len(image_data)} bytes")
        
        if len(image_data) == 0:
            raise HTTPException(status_code=400, detail="Uploaded file is empty")
        
        # Process with Gemini using raw binary data
        logger.info("Calling create_answer_key_from_image...")
        result = create_answer_key_from_image(image_data)
        logger.info(f"Gemini processing completed - result keys: {list(result.keys())}")
        
        if "error" in result:
            logger.error(f"Error in Gemini result: {result['error']}")
            raise HTTPException(status_code=500, detail=f"Gemini processing error: {result['error']}")
        
        # Create answer key record
        logger.info("Creating database record...")
        db_answer_key = AnswerKey(
            name=name,
            description=description or result.get("description"),
            answer_key=json.dumps(result.get("answer_key", {}))
        )
        db.add(db_answer_key)
        db.commit()
        db.refresh(db_answer_key)
        logger.info(f"Database record created with ID: {db_answer_key.id}")
        
        # Convert JSON string back to dict
        db_answer_key.answer_key = json.loads(db_answer_key.answer_key)
        
        logger.info("Answer key creation successful")
        return db_answer_key
    
    except HTTPException:
        raise
    except Exception as e:
        error_msg = f"Error creating answer key: {str(e)}"
        error_traceback = traceback.format_exc()
        logger.error(f"{error_msg}\n{error_traceback}")
        raise HTTPException(status_code=500, detail=f"{error_msg}\nTraceback: {error_traceback}")


@app.get("/api/answer-keys", response_model=List[AnswerKeyResponse])
def get_answer_keys(db: Session = Depends(get_db)):
    answer_keys = db.query(AnswerKey).all()
    for key in answer_keys:
        key.answer_key = json.loads(key.answer_key)
    return answer_keys


@app.get("/api/answer-keys/{key_id}", response_model=AnswerKeyResponse)
def get_answer_key(key_id: int, db: Session = Depends(get_db)):
    answer_key = db.query(AnswerKey).filter(AnswerKey.id == key_id).first()
    if not answer_key:
        raise HTTPException(status_code=404, detail="Answer key not found")
    answer_key.answer_key = json.loads(answer_key.answer_key)
    return answer_key


@app.delete("/api/answer-keys/{key_id}")
def delete_answer_key(key_id: int, db: Session = Depends(get_db)):
    answer_key = db.query(AnswerKey).filter(AnswerKey.id == key_id).first()
    if not answer_key:
        raise HTTPException(status_code=404, detail="Answer key not found")
    db.delete(answer_key)
    db.commit()
    return {"message": "Answer key deleted successfully"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

