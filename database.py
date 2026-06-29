import datetime
import json
from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

# SQLite Database URL
SQLALCHEMY_DATABASE_URL = "sqlite:///./math_question_bank.db"

engine = create_engine(
    SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False}
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

class Question(Base):
    __tablename__ = "questions"

    id = Column(Integer, primary_key=True, index=True)
    content = Column(Text, nullable=False)  # 题干 (LaTeX + markdown)
    question_type = Column(String(50), default="single_choice", index=True)  # single_choice, multi_choice, fill_in_blank, detailed_answer
    category_compulsory = Column(String(100), default="", index=True)  # 必修/选修/选择性必修
    category_chapter = Column(String(100), default="", index=True)  # 章节
    category_knowledge = Column(String(100), default="", index=True)  # 知识点
    difficulty = Column(String(50), default="medium", index=True)  # easy, medium, hard
    source = Column(String(200), default="")  # 来源
    answer_markdown = Column(Text, default="")  # 答案与解析 (LaTeX + markdown)
    review = Column(Text, default="")  # 评述 (允许空白)
    association_group_id = Column(String(100), default="", index=True)  # 关联题目分组ID (支持传递关系)
    _image_paths = Column(Text, default="[]", name="image_paths")  # 以JSON字符串形式存储相对路径列表
    tikz_code = Column(Text, default="")  # TikZ 几何绘图源代码
    tags = Column(Text, default="")  # 自定义标签 (逗号分隔或字符串)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    @property
    def image_paths(self):
        try:
            return json.loads(self._image_paths)
        except Exception:
            return []

    @image_paths.setter
    def image_paths(self, value):
        if isinstance(value, list):
            self._image_paths = json.dumps(value)
        else:
            self._image_paths = "[]"

    def to_dict(self):
        return {
            "id": self.id,
            "content": self.content,
            "question_type": self.question_type,
            "category_compulsory": self.category_compulsory,
            "category_chapter": self.category_chapter,
            "category_knowledge": self.category_knowledge,
            "difficulty": self.difficulty,
            "source": self.source,
            "answer_markdown": self.answer_markdown,
            "review": self.review,
            "association_group_id": self.association_group_id,
            "image_paths": self.image_paths,
            "tikz_code": self.tikz_code,
            "tags": self.tags,
            "created_at": (self.created_at.isoformat() + "Z") if self.created_at else None
        }

    def to_summary_dict(self):
        return {
            "id": self.id,
            "content": self.content,
            "question_type": self.question_type,
            "category_compulsory": self.category_compulsory,
            "category_chapter": self.category_chapter,
            "category_knowledge": self.category_knowledge,
            "difficulty": self.difficulty,
            "source": self.source,
            "association_group_id": self.association_group_id,
            "image_paths": self.image_paths,
            "tags": self.tags,
            "created_at": (self.created_at.isoformat() + "Z") if self.created_at else None
        }

# Dependency to get db session
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# Create tables
def init_db():
    Base.metadata.create_all(bind=engine)
    # Create indexes manually and execute automatic migrations for SQLite databases to ensure maximum performance at scale
    try:
        from sqlalchemy import text
        with engine.begin() as conn:
            # Check column existence
            cursor = conn.execute(text("PRAGMA table_info(questions)"))
            columns = [row[1] for row in cursor.fetchall()]
            
            if "review" not in columns:
                conn.execute(text("ALTER TABLE questions ADD COLUMN review TEXT DEFAULT ''"))
                print("Added column 'review' to questions table successfully.")
                
            if "association_group_id" not in columns:
                conn.execute(text("ALTER TABLE questions ADD COLUMN association_group_id VARCHAR(100) DEFAULT ''"))
                print("Added column 'association_group_id' to questions table successfully.")
                
            if "tikz_code" not in columns:
                conn.execute(text("ALTER TABLE questions ADD COLUMN tikz_code TEXT DEFAULT ''"))
                print("Added column 'tikz_code' to questions table successfully.")
                
            if "tags" not in columns:
                conn.execute(text("ALTER TABLE questions ADD COLUMN tags TEXT DEFAULT ''"))
                print("Added column 'tags' to questions table successfully.")
                
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_questions_category_compulsory ON questions (category_compulsory)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_questions_category_chapter ON questions (category_chapter)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_questions_category_knowledge ON questions (category_knowledge)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_questions_question_type ON questions (question_type)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_questions_difficulty ON questions (difficulty)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_questions_association_group_id ON questions (association_group_id)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_questions_tags ON questions (tags)"))
    except Exception as e:
        print(f"Error creating indexes or running migrations: {e}")
