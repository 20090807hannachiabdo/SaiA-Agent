CREATE TABLE IF NOT EXISTS user_preferences (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    theme TEXT DEFAULT 'light',
    language TEXT DEFAULT 'en',
    user_nickname TEXT DEFAULT '',
    selected_model TEXT DEFAULT 'gemini-3.5-flash-lite',
    active_role TEXT DEFAULT 'student'
);
INSERT OR IGNORE INTO user_preferences (id) VALUES (1);

CREATE TABLE IF NOT EXISTS ai_model (
    id TEXT PRIMARY KEY,
    model_name TEXT NOT NULL,
    description TEXT,
    version TEXT,
    last_updated DATETIME DEFAULT CURRENT_TIMESTAMP
);
INSERT OR IGNORE INTO ai_model (id, model_name, description, version) VALUES
('gemini-3.5-flash-lite', 'Gemini 3.5 Flash Lite', 'Ultra-fast, tool-optimized model', '3.5.0'),
('gemini-3.5-flash', 'Gemini 3.5 Flash', 'Deep reasoning and workflow orchestration', '3.5.0'),
('gemma-2-2b-it', 'Gemma 2 2B (Local/Fast)', 'Google ultra-lightweight & fast open model', '2.0.0'),
('gemma-2-9b-it', 'Gemma 2 9B (Local/Smart)', 'Google high-performance balanced open model', '2.0.0'),
('gemma-2-27b-it', 'Gemma 2 27B (Local/Pro)', 'Google high-capacity deep reasoning open model', '2.0.0');

CREATE TABLE IF NOT EXISTS specialist_roles (
    id TEXT PRIMARY KEY,
    name_ar TEXT NOT NULL,
    name_en TEXT NOT NULL,
    icon TEXT DEFAULT '⚡',
    system_prompt_addon TEXT NOT NULL,
    is_active INTEGER DEFAULT 1
);

INSERT OR IGNORE INTO specialist_roles (id, name_ar, name_en, icon, system_prompt_addon) VALUES
('student', 'طالب متفوق دراسياً', 'Genius Student', '🎓', 
 'ROLE: Academic Genius & Master Tutor. Approach topics using the Feynman Technique and structured proofs. Provide step-by-step rigorous breakdowns, interactive self-test flashcards, smart memory mnemonics, and conceptual clarity to help the user achieve perfect academic performance.'),
('coder', 'مهندس برمجيات محترف', 'Senior Coder', '💻', 
 'ROLE: Principal Software Engineer. Focus on clean architecture, production-ready code, Big-O efficiency, error resilience, security best practices, and idiomatic implementations.'),
('thinker', 'المفكر الاستراتيجي', 'Deep Thinker', '🧠', 
 'ROLE: Strategic Analyst & Philosophy Thinker. Break down intricate problems into first principles, analyze cognitive biases, formulate Socratic questions, and map edge-case risks.'),
('career', 'مرشد مهني واحترافي', 'Career Mentor', '🧭', 
 'ROLE: Executive Career Coach. Deliver actionable career progression strategies, optimize resumes for ATS, conduct STAR-method interview simulations, and identify high-leverage skill acquisitions.'),
('general', 'المساعد الشامل', 'General Assistant', '⚡', 
 'ROLE: Universal Autonomous Assistant. Fast, direct, versatile, and dedicated to executing user instructions safely on Windows.');

CREATE TABLE IF NOT EXISTS chat_sessions (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    role_id TEXT DEFAULT 'student',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS chat_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    role TEXT CHECK(role IN ('user', 'assistant', 'system')),
    content TEXT NOT NULL,
    file_path TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(session_id) REFERENCES chat_sessions(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS user_longterm_memory (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    category TEXT DEFAULT 'general',
    memory_key TEXT NOT NULL UNIQUE,
    memory_value TEXT NOT NULL,
    importance INTEGER DEFAULT 3,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS scheduled_tasks (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    trigger_type TEXT DEFAULT 'interval',
    trigger_value TEXT NOT NULL,
    prompt TEXT NOT NULL,
    role_id TEXT DEFAULT 'student',
    is_active INTEGER DEFAULT 1,
    last_run DATETIME,
    next_run DATETIME,
    workflow_json TEXT
);
