import google.generativeai as genai
import json
import os
import logging
import traceback
from typing import Dict, Any, Optional
from dotenv import load_dotenv
from PIL import Image
from io import BytesIO

load_dotenv()

# Setup logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
    logger.info("Gemini API configured successfully")
else:
    logger.warning("GEMINI_API_KEY not found in environment variables")


def process_omr_sheet(image_path: str, template_answer_key: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Process OMR sheet image using Gemini AI
    """
    if not GEMINI_API_KEY:
        raise ValueError("GEMINI_API_KEY not found in environment variables")
    
    model = genai.GenerativeModel('gemini-2.5-flash')
    
    # Prepare prompt
    prompt = """Analyze this OMR (Optical Mark Recognition) sheet image and extract the following information:

1. Student/Exam Details:
   - Name
   - Roll Number
   - Exam Date (if visible)
   - Any other identifying information

2. Question Responses:
   - For each question, identify which option (A, B, C, D, etc.) is marked
   - If multiple options are marked, note it
   - If no option is marked, note it as unanswered

3. Return the result as a JSON object with the following structure:
{
  "student_name": "extracted name or null",
  "roll_number": "extracted roll number or null",
  "exam_date": "extracted date or null",
  "other_details": {
    "any additional fields": "values"
  },
  "responses": {
    "1": "A",
    "2": "B",
    "3": "C",
    ...
  }
}

Focus on accuracy. If a bubble looks partially filled or unclear, note it in the response."""
    
    if template_answer_key:
        prompt += f"\n\nTemplate Answer Key (for reference): {json.dumps(template_answer_key)}"
    
    # Read and process image
    try:
        # Open image using PIL
        img = Image.open(image_path)
        
        response = model.generate_content([prompt, img])
        
        # Parse JSON from response
        response_text = response.text.strip()
        
        # Try to extract JSON from the response
        if "```json" in response_text:
            json_start = response_text.find("```json") + 7
            json_end = response_text.find("```", json_start)
            response_text = response_text[json_start:json_end].strip()
        elif "```" in response_text:
            json_start = response_text.find("```") + 3
            json_end = response_text.find("```", json_start)
            response_text = response_text[json_start:json_end].strip()
        
        result = json.loads(response_text)
        return result
    
    except json.JSONDecodeError as e:
        # Fallback: try to extract JSON manually
        import re
        json_match = re.search(r'\{.*\}', response_text, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group())
            except:
                pass
        
        # If parsing fails, return a structured error response
        return {
            "student_name": None,
            "roll_number": None,
            "exam_date": None,
            "other_details": {},
            "responses": {},
            "error": f"Failed to parse Gemini response: {str(e)}",
            "raw_response": response_text
        }
    except Exception as e:
        return {
            "student_name": None,
            "roll_number": None,
            "exam_date": None,
            "other_details": {},
            "responses": {},
            "error": f"Error processing image: {str(e)}"
        }


def create_answer_key_from_image(image_data: bytes) -> Dict[str, Any]:
    """
    Create answer key from raw image binary data
    """
    logger.info(f"create_answer_key_from_image called with {len(image_data)} bytes of image data")
    
    if not GEMINI_API_KEY:
        error_msg = "GEMINI_API_KEY not found in environment variables"
        logger.error(error_msg)
        raise ValueError(error_msg)
    
    response_text = ""  # Initialize to avoid NameError
    try:
        logger.info("Initializing Gemini model...")
        model = genai.GenerativeModel('gemini-2.0-flash')
        logger.info("Gemini model initialized")
        
        prompt = """Analyze this OMR sheet image which contains the answer key/correct answers.

Extract all the marked answers for each question and return as a JSON object:
{
  "answer_key": {
    "1": "A",
    "2": "B",
    "3": "C",
    ...
  },
  "total_questions": <number>,
  "description": "brief description if visible"
}

Return only valid JSON."""
        
        logger.info("Opening image from binary data...")
        # Open image from binary data using PIL
        img = Image.open(BytesIO(image_data))
        logger.info(f"Image opened successfully - format: {img.format}, size: {img.size}, mode: {img.mode}")
        
        logger.info("Sending request to Gemini API...")
        response = model.generate_content([prompt, img])
        logger.info("Received response from Gemini API")
        
        response_text = response.text.strip()
        logger.info(f"Response text length: {len(response_text)} characters")
        logger.debug(f"Response text preview (first 500 chars): {response_text[:500]}")
        
        # Extract JSON
        if "```json" in response_text:
            json_start = response_text.find("```json") + 7
            json_end = response_text.find("```", json_start)
            response_text = response_text[json_start:json_end].strip()
            logger.info("Extracted JSON from ```json``` block")
        elif "```" in response_text:
            json_start = response_text.find("```") + 3
            json_end = response_text.find("```", json_start)
            response_text = response_text[json_start:json_end].strip()
            logger.info("Extracted JSON from ``` block")
        else:
            logger.info("No code block markers found, using full response text")
        
        logger.info("Parsing JSON...")
        result = json.loads(response_text)
        logger.info(f"JSON parsed successfully - keys: {list(result.keys())}")
        return result
    
    except json.JSONDecodeError as e:
        error_msg = f"JSON decode error: {str(e)}\nResponse text: {response_text[:1000] if response_text else 'No response received'}"
        logger.error(error_msg)
        logger.error(traceback.format_exc())
        return {
            "answer_key": {},
            "total_questions": 0,
            "error": f"Failed to parse JSON from Gemini response: {str(e)}. Response preview: {response_text[:200] if response_text else 'No response received'}"
        }
    except Exception as e:
        error_msg = f"Error processing answer key: {str(e)}"
        error_traceback = traceback.format_exc()
        logger.error(error_msg)
        logger.error(error_traceback)
        return {
            "answer_key": {},
            "total_questions": 0,
            "error": f"{error_msg}\nTraceback: {error_traceback}"
        }


def extract_name_from_image(image_data: bytes) -> Dict[str, Any]:
    """Extract student's name, USN, and exam date from a cropped image region."""
    if not GEMINI_API_KEY:
        raise ValueError("GEMINI_API_KEY not found in environment variables")

    model = genai.GenerativeModel('gemini-2.0-flash')
    prompt = (
        "Extract the student's name, USN (University Seat Number), and exam date from this cropped exam image. "
        "Return strict JSON: {\n  \"student_name\": string | null,\n  \"roll_number\": string | null (this is USN),\n  \"exam_date\": string | null (format: DD/MM/YYYY or DD-MM-YYYY)\n}. "
        "If uncertain about any field, use null for that field. Extract date in DD/MM/YYYY or DD-MM-YYYY format if present."
    )
    try:
        img = Image.open(BytesIO(image_data))
        response = model.generate_content([prompt, img])
        text = response.text.strip()
        if "```json" in text:
            s = text.find("```json") + 7
            e = text.find("```", s)
            text = text[s:e].strip()
        elif "```" in text:
            s = text.find("```") + 3
            e = text.find("```", s)
            text = text[s:e].strip()
        result = json.loads(text)
        # Ensure all expected fields exist
        if "exam_date" not in result:
            result["exam_date"] = None
        return result
    except Exception as e:
        return {"student_name": None, "roll_number": None, "exam_date": None, "error": str(e)}


def process_omr_from_image_data(image_data: bytes, template_answer_key: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Same as process_omr_sheet but accepts raw image bytes (e.g., cropped region)."""
    if not GEMINI_API_KEY:
        raise ValueError("GEMINI_API_KEY not found in environment variables")

    model = genai.GenerativeModel('gemini-2.0-flash')

    prompt = (
        "Analyze this cropped OMR region and extract question responses. There are 50 questions 10 in one column and 5 columns This is OMR Sheet so check that grey scaling part all that and give accurate response"
        "Return strict JSON with 'responses' mapping question numbers to selected options."
    )
    if template_answer_key:
        prompt += f"\n\nTemplate Answer Key (for reference): {json.dumps(template_answer_key)}"

    try:
        img = Image.open(BytesIO(image_data))
        response = model.generate_content([prompt, img])
        text = response.text.strip()
        if "```json" in text:
            s = text.find("```json") + 7
            e = text.find("```", s)
            text = text[s:e].strip()
        elif "```" in text:
            s = text.find("```") + 3
            e = text.find("```", s)
            text = text[s:e].strip()
        return json.loads(text)
    except Exception as e:
        return {"responses": {}, "error": str(e)}