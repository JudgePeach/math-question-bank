import datetime
import sqlite3
import json
from pathlib import Path
from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    event,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import declarative_base, sessionmaker
from mathbank.paths import DATABASE_FILE, sqlite_url

# SQLite Database URL
SQLALCHEMY_DATABASE_URL = sqlite_url(DATABASE_FILE)

engine = create_engine(
    SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False}
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


@event.listens_for(Engine, "connect")
def _configure_sqlite_connection(dbapi_connection, _connection_record):
    """Apply relational safety settings to every SQLite connection."""

    if not isinstance(dbapi_connection, sqlite3.Connection):
        return
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=5000")
    finally:
        cursor.close()


def _utcnow_naive():
    """Return UTC without tzinfo for the existing SQLite DateTime columns."""

    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)


def configure_sqlite_wal(database_engine: Engine) -> str | None:
    """Enable and verify WAL mode for a persistent SQLite database."""

    database_name = database_engine.url.database
    if not database_name or database_name == ":memory:":
        return None
    with database_engine.connect().execution_options(
        isolation_level="AUTOCOMMIT"
    ) as connection:
        mode = str(
            connection.exec_driver_sql("PRAGMA journal_mode=WAL").scalar_one()
        ).lower()
        if mode != "wal":
            raise RuntimeError(f"SQLite WAL 模式启用失败，当前模式: {mode}")
        connection.exec_driver_sql("PRAGMA synchronous=NORMAL")
        connection.exec_driver_sql("PRAGMA wal_autocheckpoint=1000")
    try:
        Path(database_name).resolve().chmod(0o600)
    except OSError:
        pass
    return mode

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
    figure_align = Column(String(50), default="right")  # 插图排版位置: right (题干右侧), center (下方居中), bottom_right (下方居右)
    tags = Column(Text, default="")  # 自定义标签 (逗号分隔或字符串)
    usage_count = Column(Integer, default=0, index=True)  # 组卷引用次数
    created_at = Column(DateTime, default=_utcnow_naive)

    @property
    def image_paths(self):
        try:
            value = json.loads(self._image_paths)
            return value if isinstance(value, list) else []
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
            "has_answer": bool((self.answer_markdown or "").strip()),
            "review": self.review,
            "association_group_id": self.association_group_id,
            "image_paths": self.image_paths,
            "tikz_code": self.tikz_code,
            "figure_align": self.figure_align or "right",
            "tags": self.tags,
            "usage_count": self.usage_count or 0,
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
            "has_answer": bool((self.answer_markdown or "").strip()),
            "association_group_id": self.association_group_id,
            "image_paths": self.image_paths,
            "tikz_code": self.tikz_code,
            "figure_align": self.figure_align or "right",
            "tags": self.tags,
            "usage_count": self.usage_count or 0,
            "created_at": (self.created_at.isoformat() + "Z") if self.created_at else None
        }

class QuestionCurriculum(Base):
    __tablename__ = "question_curriculums"

    __table_args__ = (
        UniqueConstraint(
            "question_id",
            "version_code",
            name="uq_question_curriculum_version",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    question_id = Column(
        Integer,
        ForeignKey("questions.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    version_code = Column(String(50), index=True, nullable=False)  # 'A', 'B', 'S'
    compulsory = Column(String(100), default="", index=True)
    chapter = Column(String(100), default="", index=True)
    knowledge = Column(String(100), default="", index=True)

    def to_dict(self):
        return {
            "id": self.id,
            "question_id": self.question_id,
            "version_code": self.version_code,
            "compulsory": self.compulsory,
            "chapter": self.chapter,
            "knowledge": self.knowledge
        }

class Paper(Base):
    __tablename__ = "papers"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(200), nullable=False)
    subtitle = Column(String(200), default="")
    paper_type = Column(String(50), default="exam")  # exam, quiz, handout
    total_score = Column(Integer, default=150)
    metadata_json = Column(Text, default="{}")
    created_at = Column(DateTime, default=_utcnow_naive)

    def to_dict(self):
        meta = {}
        try:
            parsed_meta = json.loads(self.metadata_json or "{}")
            meta = parsed_meta if isinstance(parsed_meta, dict) else {}
        except Exception:
            meta = {}
        return {
            "id": self.id,
            "title": self.title,
            "subtitle": self.subtitle,
            "paper_type": self.paper_type,
            "total_score": self.total_score,
            "show_secret": meta.get("show_secret", True),
            "show_notice": meta.get("show_notice", True),
            "metadata_json": self.metadata_json,
            "created_at": (self.created_at.isoformat() + "Z") if self.created_at else None
        }

class PaperQuestion(Base):
    __tablename__ = "paper_questions"

    __table_args__ = (
        UniqueConstraint("paper_id", "order_index", name="uq_paper_question_order"),
        CheckConstraint("score >= 0", name="ck_paper_question_score_nonnegative"),
    )

    id = Column(Integer, primary_key=True, index=True)
    paper_id = Column(
        Integer,
        ForeignKey("papers.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    question_id = Column(
        Integer,
        ForeignKey("questions.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    order_index = Column(Integer, default=0)
    score = Column(Integer, default=5)

    def to_dict(self):
        return {
            "id": self.id,
            "paper_id": self.paper_id,
            "question_id": self.question_id,
            "order_index": self.order_index,
            "score": self.score
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
    from mathbank.db_migrations import (
        LATEST_SCHEMA_VERSION,
        REQUIRED_TABLES,
        create_pre_migration_backup,
        migrate_database,
        schema_version,
    )

    # Refuse a future schema before create_all or any legacy ALTER can mutate it.
    current_version = schema_version(engine)
    if current_version > LATEST_SCHEMA_VERSION:
        raise RuntimeError(
            f"数据库版本 {current_version} 高于程序支持版本 "
            f"{LATEST_SCHEMA_VERSION}，请升级程序。"
        )
    with engine.connect() as connection:
        existing_tables = {
            row[0]
            for row in connection.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        core_tables = existing_tables & REQUIRED_TABLES
        if existing_tables:
            upgradeable_layouts = (
                {"questions"},
                {"questions", "question_curriculums"},
                REQUIRED_TABLES,
            )
            if current_version != 0:
                upgradeable_layouts = (REQUIRED_TABLES,)
            if core_tables not in upgradeable_layouts:
                missing = ", ".join(sorted(REQUIRED_TABLES - core_tables))
                raise RuntimeError(f"数据库结构不完整，缺少必要数据表: {missing}")

            # Old releases legitimately had only the question tables.  Accept
            # those known layouts, but reject a similarly named damaged table
            # before create_all can disguise the missing core columns.
            required_columns = {
                "questions": {
                    "id", "content", "question_type", "category_compulsory",
                    "category_chapter", "category_knowledge", "difficulty",
                    "source", "answer_markdown", "image_paths", "created_at",
                },
                "question_curriculums": {
                    "id", "question_id", "version_code", "compulsory",
                    "chapter", "knowledge",
                },
                "papers": {
                    "id", "title", "subtitle", "paper_type", "total_score",
                    "metadata_json", "created_at",
                },
                "paper_questions": {
                    "id", "paper_id", "question_id", "order_index", "score",
                },
            }
            for table_name in core_tables:
                columns = {
                    row[1]
                    for row in connection.exec_driver_sql(
                        f'PRAGMA table_info("{table_name}")'
                    ).fetchall()
                }
                missing_columns = required_columns[table_name] - columns
                if missing_columns:
                    missing = ", ".join(sorted(missing_columns))
                    raise RuntimeError(
                        f"数据库表 {table_name} 缺少核心字段: {missing}"
                    )
    pre_migration_backup = None
    if current_version < LATEST_SCHEMA_VERSION and existing_tables:
        pre_migration_backup = create_pre_migration_backup(
            engine,
            from_version=current_version,
            to_version=LATEST_SCHEMA_VERSION,
        )

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

            if "figure_align" not in columns:
                conn.execute(text("ALTER TABLE questions ADD COLUMN figure_align VARCHAR(50) DEFAULT 'right'"))
                print("Added column 'figure_align' to questions table successfully.")
                
            if "tags" not in columns:
                conn.execute(text("ALTER TABLE questions ADD COLUMN tags TEXT DEFAULT ''"))
                print("Added column 'tags' to questions table successfully.")
                
            if "usage_count" not in columns:
                conn.execute(text("ALTER TABLE questions ADD COLUMN usage_count INTEGER DEFAULT 0"))
                print("Added column 'usage_count' to questions table successfully.")
                
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_questions_category_compulsory ON questions (category_compulsory)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_questions_category_chapter ON questions (category_chapter)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_questions_category_knowledge ON questions (category_knowledge)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_questions_question_type ON questions (question_type)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_questions_difficulty ON questions (difficulty)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_questions_association_group_id ON questions (association_group_id)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_questions_tags ON questions (tags)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_questions_usage_count ON questions (usage_count)"))

            # Create indexes on question_curriculums
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_question_curriculums_lookup ON question_curriculums (version_code, compulsory, chapter, knowledge)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_question_curriculums_qid ON question_curriculums (question_id)"))

            # Auto-migrate legacy data to A-version question_curriculums
            cursor = conn.execute(text("SELECT COUNT(*) FROM question_curriculums"))
            count = cursor.fetchone()[0]
            if count == 0:
                conn.execute(text("""
                    INSERT INTO question_curriculums (question_id, version_code, compulsory, chapter, knowledge)
                    SELECT id, 'A', category_compulsory, category_chapter, category_knowledge
                    FROM questions
                """))
                print("Successfully auto-migrated legacy question categories to A-version question_curriculums mapping.")
    except Exception as e:
        raise RuntimeError("数据库旧字段或索引迁移失败，服务已停止启动") from e

    migration_result = migrate_database(
        engine, pre_migration_backup=pre_migration_backup
    )
    configure_sqlite_wal(engine)
    if migration_result.get("from_version") != migration_result.get("to_version"):
        print(f"[Database] Schema migration complete: {migration_result}")
