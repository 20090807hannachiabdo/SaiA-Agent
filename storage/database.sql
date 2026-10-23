


CREATE TABLE IF NOT EXISTS user_preferences (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    theme TEXT DEFAULT 'light',               -- light / dark
    language TEXT DEFAULT 'en',               -- en / ar / fr
    user_nickname TEXT DEFAULT '',            -- الاسم المستعار (فارغ = "أنت / You / Vous")
    selected_model TEXT DEFAULT 'saia-1'      -- معرّف النموذج المختار (ai_model.id)
);
INSERT OR IGNORE INTO user_preferences (id) VALUES (1);

-- النماذج المتاحة: أضف صفاً جديداً هنا وسيظهر تلقائياً في قائمة الاختيار بالواجهة
CREATE TABLE IF NOT EXISTS ai_model (
    id TEXT PRIMARY KEY,                -- معرف النموذج (مثل: saia-1)
    model_name TEXT NOT NULL,           -- اسم النموذج للعرض في الواجهة
    description TEXT,                   -- وصف مختصر للنموذج
    version TEXT,                       -- إصدار النموذج
    last_updated DATETIME DEFAULT CURRENT_TIMESTAMP  -- آخر تحديث للنموذج
);
INSERT OR IGNORE INTO ai_model (id, model_name, description, version) VALUES
('saia-1', 'SaiA Model 1', 'Core AI', '1.0.0');

