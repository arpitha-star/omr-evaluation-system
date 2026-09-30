from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
from datetime import datetime
import os

SQLALCHEMY_DATABASE_URL = "sqlite:///./omr_database.db"

engine = create_engine(
    SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False}
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


class Template(Base):
    __tablename__ = "templates"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    description = Column(Text)
    total_questions = Column(Integer, nullable=False)
    answer_key = Column(Text, nullable=False)  # JSON string
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationships
    omr_sheets = relationship("OMRSheet", back_populates="template")


class OMRSheet(Base):
    __tablename__ = "omr_sheets"
    
    id = Column(Integer, primary_key=True, index=True)
    template_id = Column(Integer, ForeignKey("templates.id"), nullable=False)
    student_name = Column(String)
    roll_number = Column(String)
    exam_date = Column(String)
    other_details = Column(Text)  # JSON string for additional details
    responses = Column(Text, nullable=False)  # JSON string
    image_path = Column(String)
    correct_count = Column(Integer, default=0)
    wrong_count = Column(Integer, default=0)
    total_questions = Column(Integer, default=0)
    percentage = Column(String, default="0%")
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationships
    template = relationship("Template", back_populates="omr_sheets")


class AnswerKey(Base):
    __tablename__ = "answer_keys"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    description = Column(Text)
    answer_key = Column(Text, nullable=False)  # JSON string
    created_at = Column(DateTime, default=datetime.utcnow)


def init_db():
    Base.metadata.create_all(bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

